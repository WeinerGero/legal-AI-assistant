"""Диалоги, сообщения и опрос статуса запроса (ARCHITECTURE.md §11.2).

``POST`` в одной транзакции проверяет владельца, фиксирует сообщение
со стабильным ``client_message_id`` и создаёт единственное задание.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["conversations"])


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class CreateConversationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_message_id: str
    text: str = Field(min_length=1, max_length=8_000)
    response_language: Literal["ru"] = "ru"


class SendMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    user_message_id: str
    status: Literal["pending"] = "pending"


class RequestStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["pending", "running", "completed", "failed"]
    result: dict | None = None


@router.get("/conversations")
def list_conversations(limit: int = 20, cursor: str | None = None) -> dict:
    raise NotImplementedError("backend.api.conversations.service.list")


@router.post(
    "/conversations",
    response_model=CreateConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(payload: CreateConversationRequest) -> CreateConversationResponse:
    raise NotImplementedError("backend.api.conversations.service.create")


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    raise NotImplementedError("backend.api.conversations.service.get")


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: str, payload: CreateConversationRequest) -> dict:
    raise NotImplementedError("backend.api.conversations.service.update")


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str) -> None:
    raise NotImplementedError("backend.api.conversations.service.delete")


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, limit: int = 20, cursor: str | None = None) -> dict:
    raise NotImplementedError("backend.api.conversations.service.messages")


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def send_message(
    conversation_id: str, payload: SendMessageRequest
) -> SendMessageResponse:
    """Идемпотентная постановка задания; повтор с тем же текстом даёт тот же ``request_id``."""
    raise NotImplementedError("backend.api.conversations.service.enqueue")


@router.get("/requests/{request_id}", response_model=RequestStatusResponse)
def get_request(request_id: str) -> RequestStatusResponse:
    raise NotImplementedError("backend.api.conversations.service.status")
