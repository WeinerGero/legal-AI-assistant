from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from backend.core_gateway import CoreUnavailable, answer_question
from backend.database import (
    Answer,
    Base,
    Conversation,
    LoginSession,
    Message,
    SessionLocal,
    User,
    engine,
    get_db,
)
from core.contracts import (
    AnalysisRequestV1,
    ChatMessage,
    DialogContext,
    RenderedAnswerV1,
    TechnicalFailure,
)

SESSION_TTL = timedelta(hours=12)
PASSWORD_ITERATIONS = 310_000
bearer_scheme = HTTPBearer(auto_error=False)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(InputModel):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("login")
    @classmethod
    def strip_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Login must not be blank.")
        return value


class ConversationInput(InputModel):
    title: str = Field(default="Новый диалог", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank.")
        return value


class ConversationUpdate(InputModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank.")
        return value


class MessageInput(InputModel):
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank.")
        return value


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${derived.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), expected_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _seed_user() -> None:
    login = os.getenv("APP_USER_LOGIN", "").strip()
    password = os.getenv("APP_USER_PASSWORD", "")
    if not login or not password:
        raise RuntimeError("APP_USER_LOGIN and APP_USER_PASSWORD must be configured.")

    with SessionLocal() as db:
        users = db.scalars(select(User)).all()
        if len(users) > 1:
            raise RuntimeError("The MVP database must contain exactly one user.")

        if users:
            user = users[0]
            user.login = login
            user.role = "user"
            if not _verify_password(password, user.password_hash):
                user.password_hash = _hash_password(password)
        else:
            db.add(User(login=login, password_hash=_hash_password(password), role="user"))
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    _seed_user()
    yield


app = FastAPI(title="Legal Assistant API", version="1.0.0", lifespan=lifespan)
app.state.core_handler = None


def _current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")

    session = db.get(LoginSession, _token_hash(credentials.credentials))
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")
    if session.expires_at <= datetime.now(timezone.utc).timestamp():
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired.")

    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")
    return user


def _owned_conversation(db: Session, user_id: UUID, conversation_id: UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


def _conversation_json(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": str(conversation.id),
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _message_json(message: Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "role": message.role,
        "content": message.content,
        "answer": message.answer_json,
        "created_at": message.created_at.isoformat(),
    }


def _answer_text(answer: RenderedAnswerV1) -> str:
    return "\n\n".join(
        segment.text for block in answer.blocks for segment in block.segments
    )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/api/v1/auth/login")
def login(payload: LoginInput, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.login == payload.login))
    if user is None or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login or password."
        )

    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=(datetime.now(timezone.utc) + SESSION_TTL).timestamp(),
        )
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": int(SESSION_TTL.total_seconds()),
        "user": {"id": str(user.id), "login": user.login, "role": user.role},
    }


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> None:
    if credentials is not None:
        session = db.get(LoginSession, _token_hash(credentials.credentials))
        if session is not None:
            db.delete(session)
            db.commit()


@app.get("/api/v1/auth/me")
def current_profile(user: User = Depends(_current_user)) -> dict[str, str]:
    return {"id": str(user.id), "login": user.login, "role": user.role}


@app.get("/api/v1/conversations")
def list_conversations(
    search: str | None = Query(default=None, max_length=200),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(Conversation).where(Conversation.user_id == user.id)
    if search:
        statement = statement.where(Conversation.title.ilike(f"%{search}%"))
    statement = statement.order_by(Conversation.updated_at.desc())
    return [_conversation_json(item) for item in db.scalars(statement).all()]


@app.post("/api/v1/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationInput,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conversation = Conversation(user_id=user.id, title=payload.title.strip())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_json(conversation)


@app.get("/api/v1/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _conversation_json(_owned_conversation(db, user.id, conversation_id))


@app.patch("/api/v1/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conversation = _owned_conversation(db, user.id, conversation_id)
    conversation.title = payload.title.strip()
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    return _conversation_json(conversation)


@app.delete("/api/v1/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: UUID,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation = _owned_conversation(db, user.id, conversation_id)
    message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
    db.execute(delete(Answer).where(Answer.message_id.in_(message_ids)))
    db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    db.delete(conversation)
    db.commit()


@app.get("/api/v1/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _owned_conversation(db, user.id, conversation_id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    messages = list(db.scalars(statement).all())
    return [_message_json(item) for item in reversed(messages)]


@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
def send_message(
    conversation_id: UUID,
    payload: MessageInput,
    request: Request,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conversation = _owned_conversation(db, user.id, conversation_id)
    recent = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(6)
    ).all()
    analysis_request = AnalysisRequestV1(
        current_message=payload.content,
        dialog_context=DialogContext.model_validate(conversation.context_json),
        recent_messages=[
            ChatMessage(role=item.role, content=item.content) for item in reversed(recent)
        ],
        current_date=date.today(),
    )

    user_message = Message(
        conversation_id=conversation_id,
        role="user",
        content=payload.content,
    )
    db.add(user_message)
    if conversation.title == "Новый диалог":
        conversation.title = payload.content.strip()[:80] or "Новый диалог"
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user_message)

    try:
        answer = answer_question(analysis_request, request.app.state.core_handler)
    except CoreUnavailable as exc:
        failure = TechnicalFailure(
            error_code="model_unavailable", message=str(exc), request_id=str(uuid4())
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(mode="json"),
        )
    except ValidationError:
        failure = TechnicalFailure(
            error_code="invalid_model_output",
            message="Core returned an invalid answer.",
            request_id=str(uuid4()),
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=failure.model_dump(mode="json"),
        )

    answer_id = uuid4()
    answer = answer.model_copy(update={"answer_id": str(answer_id)})
    answer_json = answer.model_dump(mode="json")
    assistant_message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=_answer_text(answer),
        answer_json=answer_json,
    )
    db.add(assistant_message)
    db.flush()
    db.add(
        Answer(
            id=answer_id,
            user_id=user.id,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            answer_json=answer_json,
        )
    )
    db.commit()
    db.refresh(assistant_message)
    return {
        "user_message": _message_json(user_message),
        "assistant_message": _message_json(assistant_message),
        "answer": answer_json,
    }


@app.get("/api/v1/answers/{answer_id}")
def get_answer(
    answer_id: UUID,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    answer = db.scalar(
        select(Answer).where(Answer.id == answer_id, Answer.user_id == user.id)
    )
    if answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found.")
    return answer.answer_json
