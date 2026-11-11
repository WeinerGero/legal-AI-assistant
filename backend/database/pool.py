"""Пул соединений и настройки БД (ARCHITECTURE.md §4.5, §16.1).

Секреты берутся только из окружения; прикладная роль не имеет
``BYPASSRLS`` и используется вместе с ``FORCE ROW LEVEL SECURITY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from os import environ


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Параметры подключения без секретов в коде и репозитории."""

    dsn: str
    pool_min: int = 1
    pool_max: int = 10
    statement_timeout_ms: int = 30_000

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        dsn = environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("не задан DATABASE_URL")
        return cls(dsn=dsn)
