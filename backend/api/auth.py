"""Маршруты аутентификации (ARCHITECTURE.md §11.1).

``POST /auth/login`` возвращает токен и профиль, ``POST /auth/logout``
аннулирует сеанс, ``GET /auth/me`` отдаёт текущий профиль.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.auth.dependencies import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    login: str
    role: str


class LoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 43_200  # 12 часов (ARCHITECTURE.md §2.2)
    user: UserProfile


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    """Проверка пароля и выдача ограниченного по времени токена сеанса."""
    raise NotImplementedError("backend.auth.service.authenticate")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser) -> Response:
    """Аннулирование текущего сеанса."""
    raise NotImplementedError("backend.auth.service.revoke_session")


@router.get("/me", response_model=UserProfile)
def me(user: CurrentUser) -> UserProfile:
    raise NotImplementedError("backend.auth.service.profile")


def _expires_at() -> datetime:
    raise NotImplementedError
