from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from backend.core_gateway import CoreUnavailable, InvalidCoreOutput, answer_question
from backend.database import (
    Answer,
    AnswerSourceContent,
    Base,
    Conversation,
    LoginSession,
    Message,
    SessionLocal,
    User,
    engine,
    get_db,
)
from backend.schemas import (
    AnswerResponse,
    CoreResult,
    ModelQuestion,
    QuestionContext,
    RecentMessage,
    SourceSnippet,
)
from core.contracts import TechnicalFailure

SESSION_TTL = timedelta(hours=12)
PASSWORD_ITERATIONS = 310_000
bearer_scheme = HTTPBearer(auto_error=False)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(InputModel):
    login: str = Field(
        min_length=1,
        max_length=128,
        description="Логин пользователя.",
        examples=["user@example.com"],
    )
    password: str = Field(
        min_length=1,
        max_length=256,
        description="Пароль пользователя.",
        examples=["<set-me>"],
    )

    @field_validator("login")
    @classmethod
    def strip_login(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Login must not be blank.")
        return value


class ConversationInput(InputModel):
    title: str = Field(
        default="Новый диалог",
        min_length=1,
        max_length=200,
        description="Название нового диалога.",
        examples=["Вопрос по договору"],
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank.")
        return value


class ConversationUpdate(InputModel):
    title: str = Field(
        min_length=1,
        max_length=200,
        description="Новое название диалога.",
        examples=["Обновлённое название"],
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank.")
        return value


class MessageInput(InputModel):
    content: str = Field(
        min_length=1,
        max_length=20_000,
        description="Текст вопроса.",
        examples=["Какие условия расторжения предусмотрены договором?"],
    )
    context: QuestionContext | None = Field(
        default=None,
        description=(
            "Необязательный контекст вопроса, например активный документ "
            "или дата события."
        ),
    )

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank.")
        return value


class HealthOutput(BaseModel):
    status: str = Field(description="Состояние сервиса.")


class UserOutput(BaseModel):
    id: UUID = Field(description="Идентификатор пользователя.")
    login: str = Field(description="Логин пользователя.")
    role: str = Field(description="Роль пользователя.")


class LoginOutput(BaseModel):
    access_token: str = Field(description="Токен авторизации.")
    token_type: Literal["bearer"] = Field(description="Тип токена авторизации.")
    expires_in: int = Field(description="Срок действия токена в секундах.")
    user: UserOutput = Field(description="Данные вошедшего пользователя.")


class ProfileOutput(BaseModel):
    id: UUID = Field(description="Идентификатор пользователя.")
    login: str = Field(description="Логин пользователя.")
    role: str = Field(description="Роль пользователя.")


class ConversationOutput(BaseModel):
    id: UUID = Field(description="Идентификатор диалога.")
    title: str = Field(description="Название диалога.")
    created_at: datetime = Field(description="Время создания диалога.")
    updated_at: datetime = Field(description="Время последнего изменения диалога.")


class MessageOutput(BaseModel):
    id: UUID = Field(description="Идентификатор сообщения.")
    role: Literal["user", "assistant"] = Field(
        description="Автор сообщения: пользователь или ассистент."
    )
    content: str = Field(description="Текст сообщения.")
    answer: AnswerResponse | None = Field(
        description="Структурированный ответ ассистента, если он есть."
    )
    created_at: datetime = Field(description="Время создания сообщения.")


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


app = FastAPI(
    title="Legal Assistant API",
    description=(
        "API юридического ассистента: авторизация, управление диалогами "
        "и получение ответов с источниками."
    ),
    version="1.0.0",
    openapi_tags=[
        {"name": "Health", "description": "Проверка доступности API и базы данных."},
        {"name": "Auth", "description": "Вход, выход и профиль пользователя."},
        {"name": "Conversations", "description": "Создание и управление диалогами."},
        {"name": "Messages", "description": "История сообщений и отправка вопросов."},
        {"name": "Answers", "description": "Получение ответов и фрагментов источников."},
    ],
    lifespan=lifespan,
)
app.state.core_handler = None


def _current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Проверяет Bearer-токен и возвращает текущего пользователя."""
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
    """Находит диалог пользователя или возвращает 404."""
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


def _answer_text(answer: AnswerResponse) -> str:
    return "\n\n".join(
        segment.text for block in answer.blocks for segment in block.segments
    )


def _conversation_result(value: str, audit_pass: str) -> CoreResult:
    return CoreResult.model_validate({
        "answer": {
            "schema_version": "1.0",
            "answer_id": str(uuid4()),
            "legal_status": "out_of_scope",
            "verification_status": "passed",
            "missing_information": [],
            "false_premises": [],
            "clarification_question": None,
            "blocks": [{"block_type": "clarification", "segments": [{"claim_id": "H001", "text": value, "citations": []}]}],
            "sources": [],
            "retrieval_audit": {"performed": False, "passes": [audit_pass]},
        },
        "source_locations": [],
    })


def _history_result(target: str, messages: list[Message]) -> CoreResult:
    users = []
    for index, item in enumerate(messages):
        if item.role != "user":
            continue
        next_item = messages[index + 1] if index + 1 < len(messages) else None
        audit = {}
        if next_item is not None and next_item.role == "assistant":
            audit = (getattr(next_item, "answer_json", None) or {}).get("retrieval_audit", {})
        if audit.get("performed") is not False:
            users.append(item.content)
    assistants = [item.content for item in messages if item.role == "assistant"]
    if target == "last_assistant":
        value = assistants[-1] if assistants else "Ранее я ещё не отвечал в этом диалоге."
    elif target == "first_user":
        value = users[0] if users else "В этом диалоге ещё нет предыдущих вопросов."
    elif target == "last_user":
        value = users[-1] if users else "В этом диалоге ещё нет предыдущих вопросов."
    else:
        raise ValueError("unknown conversation history target")
    return _conversation_result(value, "conversation_history")


@app.get(
    "/healthz",
    summary="Проверить доступность API",
    description="Возвращает успешный статус, если процесс API отвечает.",
    response_model=HealthOutput,
    response_description="API доступно.",
    tags=["Health"],
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/readyz",
    summary="Проверить готовность API",
    description="Проверяет доступность базы данных простым запросом.",
    response_model=HealthOutput,
    response_description="API и база данных готовы.",
    tags=["Health"],
    responses={503: {"description": "База данных недоступна."}},
)
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post(
    "/api/v1/auth/login",
    summary="Войти",
    description=(
        "Проверяет логин и пароль. В ответе возвращается Bearer-токен, "
        "который нужно передавать в защищённые запросы."
    ),
    response_model=LoginOutput,
    response_description="Токен авторизации и профиль пользователя.",
    tags=["Auth"],
    responses={
        401: {"description": "Неверный логин или пароль."},
        422: {"description": "Некорректное тело запроса."},
    },
)
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


@app.post(
    "/api/v1/auth/logout",
    summary="Выйти",
    description="Удаляет текущую сессию авторизации. Требуется Bearer-токен.",
    status_code=status.HTTP_204_NO_CONTENT,
    response_description="Сессия завершена; тело ответа отсутствует.",
    tags=["Auth"],
    responses={
        204: {"description": "Сессия успешно завершена."},
        401: {"description": "Токен отсутствует, истёк или недействителен."},
    },
)
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


@app.get(
    "/api/v1/auth/me",
    summary="Получить профиль",
    description="Возвращает профиль пользователя из текущей Bearer-сессии.",
    response_model=ProfileOutput,
    response_description="Профиль текущего пользователя.",
    tags=["Auth"],
    responses={401: {"description": "Токен отсутствует, истёк или недействителен."}},
)
def current_profile(user: User = Depends(_current_user)) -> dict[str, str]:
    return {"id": str(user.id), "login": user.login, "role": user.role}


@app.get(
    "/api/v1/conversations",
    summary="Список диалогов",
    description=(
        "Возвращает диалоги текущего пользователя, отсортированные по времени "
        "последнего изменения. Можно отфильтровать по названию."
    ),
    response_model=list[ConversationOutput],
    response_description="Список диалогов пользователя.",
    tags=["Conversations"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        422: {"description": "Некорректное значение параметра search."},
    },
)
def list_conversations(
    search: str | None = Query(
        default=None,
        max_length=200,
        description="Необязательная подстрока для поиска в названиях диалогов.",
        examples=["договор"],
    ),
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(Conversation).where(Conversation.user_id == user.id)
    if search:
        statement = statement.where(Conversation.title.ilike(f"%{search}%"))
    statement = statement.order_by(Conversation.updated_at.desc())
    return [_conversation_json(item) for item in db.scalars(statement).all()]


@app.post(
    "/api/v1/conversations",
    summary="Создать диалог",
    description="Создаёт новый диалог для текущего пользователя.",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationOutput,
    response_description="Созданный диалог.",
    tags=["Conversations"],
    responses={
        201: {"description": "Диалог создан."},
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        422: {"description": "Некорректное тело запроса."},
    },
)
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


@app.get(
    "/api/v1/conversations/{conversation_id}",
    summary="Получить диалог",
    description="Возвращает диалог, принадлежащий текущему пользователю.",
    response_model=ConversationOutput,
    response_description="Найденный диалог.",
    tags=["Conversations"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Диалог не найден."},
        422: {"description": "Некорректный UUID диалога."},
    },
)
def get_conversation(
    conversation_id: UUID,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _conversation_json(_owned_conversation(db, user.id, conversation_id))


@app.patch(
    "/api/v1/conversations/{conversation_id}",
    summary="Переименовать диалог",
    description="Изменяет название диалога текущего пользователя.",
    response_model=ConversationOutput,
    response_description="Диалог с обновлённым названием.",
    tags=["Conversations"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Диалог не найден."},
        422: {"description": "Некорректный UUID или тело запроса."},
    },
)
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


@app.delete(
    "/api/v1/conversations/{conversation_id}",
    summary="Удалить диалог",
    description=(
        "Удаляет диалог и связанные с ним сообщения, ответы и сохранённые "
        "фрагменты источников."
    ),
    status_code=status.HTTP_204_NO_CONTENT,
    response_description="Диалог удалён; тело ответа отсутствует.",
    tags=["Conversations"],
    responses={
        204: {"description": "Диалог успешно удалён."},
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Диалог не найден."},
        422: {"description": "Некорректный UUID диалога."},
    },
)
def delete_conversation(
    conversation_id: UUID,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation = _owned_conversation(db, user.id, conversation_id)
    answer_ids = select(Answer.id).where(Answer.conversation_id == conversation.id)
    db.execute(delete(AnswerSourceContent).where(AnswerSourceContent.answer_id.in_(answer_ids)))
    db.execute(delete(Answer).where(Answer.id.in_(answer_ids)))
    db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    db.delete(conversation)
    db.commit()


@app.get(
    "/api/v1/conversations/{conversation_id}/messages",
    summary="История сообщений",
    description=(
        "Возвращает сообщения диалога в хронологическом порядке. "
        "Максимальное значение limit — 500."
    ),
    response_model=list[MessageOutput],
    response_description="Список сообщений диалога.",
    tags=["Messages"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Диалог не найден."},
        422: {"description": "Некорректный UUID диалога или значение limit."},
    },
)
def list_messages(
    conversation_id: UUID,
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Максимальное количество сообщений в ответе (от 1 до 500).",
        examples=[100],
    ),
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


@app.post(
    "/api/v1/conversations/{conversation_id}/messages",
    summary="Отправить вопрос",
    description=(
        "Добавляет вопрос в диалог и возвращает структурированный ответ "
        "ассистента с источниками. Требуется Bearer-токен."
    ),
    status_code=status.HTTP_201_CREATED,
    response_model=AnswerResponse,
    response_description="Ответ ассистента с подтверждёнными утверждениями и источниками.",
    tags=["Messages"],
    responses={
        201: {"description": "Ответ успешно сформирован."},
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Диалог не найден."},
        422: {"description": "Некорректный UUID диалога или тело запроса."},
        status.HTTP_502_BAD_GATEWAY: {
            "model": TechnicalFailure,
            "description": "Ядро вернуло некорректный ответ или сопоставление источников.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": TechnicalFailure,
            "description": "Ядро, модели или обязательная конфигурация недоступны.",
        },
    },
)
def send_message(
    conversation_id: UUID,
    payload: MessageInput,
    request: Request,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conversation = _owned_conversation(db, user.id, conversation_id)
    recent = list(db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    ).all())
    # Повтор последнего вопроса после сбоя не дублирует его в истории.
    retrying_previous_message = bool(
        recent
        and recent[0].role == "user"
        and recent[0].content == payload.content
    )
    if retrying_previous_message:
        recent = recent[1:]
    context_data = conversation.context_json or {}
    previous_context = {
        "active_document": context_data.get("active_document"),
        "event_date": context_data.get("event_date"),
    }
    new_context = previous_context.copy()
    if payload.context is not None:
        submitted_context = payload.context.model_dump(mode="json", exclude_unset=True)
        new_context.update(submitted_context)
    context_revision = int(context_data.get("context_revision", 1))
    if previous_context != new_context:
        context_revision += 1
    conversation.context_json = {
        **context_data,
        **new_context,
        "context_revision": context_revision,
    }
    model_context = QuestionContext.model_validate(new_context)

    if not retrying_previous_message:
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

    core_handler = request.app.state.core_handler
    if core_handler is None:
        failure = TechnicalFailure(
            error_code="model_unavailable",
            message="The core answer pipeline is not configured.",
            request_id=str(uuid4()),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(mode="json"),
        )

    corpus_id = os.getenv("CORPUS_ID", "").strip()
    index_generation_id = os.getenv("INDEX_GENERATION_ID", "").strip()
    route_started = monotonic()
    route_fn = getattr(core_handler, "route_question", None)
    route = route_fn(payload.content) if callable(route_fn) else None
    if route is not None and route.route == "conversation":
        if route.action == "history":
            conversation_result = _history_result(route.history_target, list(reversed(recent)))
        else:
            conversation_result = _conversation_result(route.reply, "conversation_reply")
    else:
        conversation_result = None
    if (not corpus_id or not index_generation_id) and conversation_result is None:
        failure = TechnicalFailure(
            error_code="internal_error",
            message="CORPUS_ID and INDEX_GENERATION_ID must be configured.",
            request_id=f"req-{uuid4()}",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(mode="json"),
        )

    request_budget = getattr(getattr(core_handler, "config", None), "request_timeout_seconds", 330.0)
    model_request = ModelQuestion(
        schema_version="1.0",
        request_id=f"req-{uuid4()}",
        question=payload.content,
        context=model_context,
        recent_messages=[
            RecentMessage(role=item.role, content=item.content) for item in reversed(recent)
        ],
        summary=context_data.get("summary"),
        corpus_id=corpus_id,
        index_generation_id=index_generation_id,
        context_revision=context_revision,
        answer_style=route.answer_style if route is not None else "standard",
        remaining_budget_seconds=max(0.001, request_budget - (monotonic() - route_started)),
    )

    try:
        core_result = conversation_result or answer_question(model_request, core_handler)
    except CoreUnavailable as exc:
        failure = TechnicalFailure(
            error_code="model_unavailable", message=str(exc), request_id=str(uuid4())
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(mode="json"),
        )
    except InvalidCoreOutput:
        failure = TechnicalFailure(
            error_code="invalid_model_output",
            message="Core returned an invalid answer or source mapping.",
            request_id=f"req-{uuid4()}",
        )
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=failure.model_dump(mode="json"),
        )

    answer = core_result.answer
    answer_json = answer.model_dump(mode="json")
    assistant_message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=_answer_text(answer),
        answer_json=answer_json,
    )
    db.add(assistant_message)
    db.flush()
    answer_record = Answer(
        id=uuid4(),
        user_id=user.id,
        conversation_id=conversation_id,
        message_id=assistant_message.id,
        answer_json=answer_json,
    )
    db.add(answer_record)
    db.flush()
    for location in core_result.source_locations:
        db.add(
            AnswerSourceContent(
                answer_id=answer_record.id,
                answer_source_id=location.answer_source_id,
                document_id=location.document_id,
                document_version_id=location.document_version_id,
                clause_id=location.clause_id,
                canonical_text=location.canonical_text,
                start_char=location.start_char,
                end_char=location.end_char,
            )
        )
    db.commit()
    db.refresh(assistant_message)
    return answer_json


@app.get(
    "/api/v1/answers/{answer_id}",
    summary="Получить ответ",
    description="Возвращает сохранённый ответ, принадлежащий текущему пользователю.",
    response_model=AnswerResponse,
    response_description="Структурированный ответ с источниками.",
    tags=["Answers"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Ответ не найден."},
        422: {"description": "Некорректный идентификатор ответа."},
    },
)
def get_answer(
    answer_id: str,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    answer = db.scalar(
        select(Answer).where(
            Answer.user_id == user.id,
            Answer.answer_json["answer_id"].as_string() == answer_id,
        )
    )
    if answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found.")
    return answer.answer_json


@app.get(
    "/api/v1/answers/{answer_id}/sources/{answer_source_id}",
    summary="Получить фрагмент источника",
    description=(
        "Возвращает сохранённый фрагмент документа с текстом до и после "
        "подсвеченного отрывка."
    ),
    response_model=SourceSnippet,
    response_description="Фрагмент источника и окружающий его текст.",
    tags=["Answers"],
    responses={
        401: {"description": "Токен отсутствует, истёк или недействителен."},
        404: {"description": "Ответ или источник не найден."},
        422: {"description": "Некорректный идентификатор ответа."},
        500: {"description": "Сохранённая позиция источника некорректна."},
    },
)
def get_answer_source(
    answer_id: str,
    answer_source_id: str,
    user: User = Depends(_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    answer = db.scalar(
        select(Answer).where(
            Answer.user_id == user.id,
            Answer.answer_json["answer_id"].as_string() == answer_id,
        )
    )
    if answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found.")

    source_info = next(
        (
            source
            for source in answer.answer_json["sources"]
            if source["answer_source_id"] == answer_source_id
        ),
        None,
    )
    source_content = db.scalar(
        select(AnswerSourceContent).where(
            AnswerSourceContent.answer_id == answer.id,
            AnswerSourceContent.answer_source_id == answer_source_id,
        )
    )
    if source_info is None or source_content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    text_value = source_content.canonical_text
    start = source_content.start_char
    end = source_content.end_char
    if not 0 <= start < end <= len(text_value):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored source location is invalid.",
        )

    return {
        **source_info,
        "before": text_value[:start],
        "highlight": text_value[start:end],
        "after": text_value[end:],
    }
