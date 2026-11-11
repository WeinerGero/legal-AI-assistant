"""Пароли и токены сеансов (ARCHITECTURE.md §2.2).

Argon2id для паролей; для токенов — случайное значение, в БД только хеш.
Сервисные функции не логируют секреты.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SESSION_TTL = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    token_hash: str
    expires_at: datetime


def new_session_token(*, now: datetime | None = None) -> IssuedSession:
    """Создаёт случайный токен и фиксирует его хеш для хранения."""
    token = secrets.token_urlsafe(32)
    issued_at = now or datetime.now(tz=timezone.utc)
    return IssuedSession(
        token=token,
        token_hash=hash_token(token),
        expires_at=issued_at + SESSION_TTL,
    )


def hash_token(token: str) -> str:
    """Детерминированный хеш токена; сам токен в БД не попадает."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str, *, salt: bytes) -> str:
    """Заглушка контракта: реальная реализация использует Argon2id."""
    raise NotImplementedError("argon2.PasswordHasher")


def verify_password(password: str, password_hash: str) -> bool:
    raise NotImplementedError("argon2.PasswordHasher")
