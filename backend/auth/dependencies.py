"""Зависимости FastAPI: извлечение и проверка текущего пользователя (§2.2).

API принимает ``Authorization: Bearer <token>``; в БД хранится только
хеш токена, срок действия — 12 часов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

SESSION_TTL_SECONDS = 43_200


@dataclass(frozen=True, slots=True)
class Principal:
    """Проверенный пользователь текущего запроса."""

    user_id: str
    login: str
    role: str


def get_current_user(authorization: Annotated[str | None, Header()] = None) -> Principal:
    """Извлекает Bearer-токен и проверяет активный сеанс."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Требуется вход."}},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пустой токен")
    raise NotImplementedError("backend.auth.service.resolve_session")


def require_admin(user: Principal = Depends(get_current_user)) -> Principal:
    """Проверка роли ``admin`` для административных маршрутов."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет права")
    return user


CurrentUser = Annotated[Principal, Depends(get_current_user)]
AdminUser = Annotated[Principal, Depends(require_admin)]
