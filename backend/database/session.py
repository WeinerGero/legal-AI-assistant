"""Транзакция с идентификатором пользователя для RLS (ARCHITECTURE.md §4.5).

В рамках одной транзакции выставляется ``SET LOCAL app.user_id``; соединения
из пула очищаются завершением транзакции.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from uuid import UUID

SET_USER_SQL = "SELECT set_config('app.user_id', $1, true)"


@contextmanager
def user_transaction(connection, user_id: UUID) -> Iterator[object]:
    """Открывает транзакцию, привязанную к проверенному пользователю."""
    with connection.transaction():
        connection.execute(SET_USER_SQL, (str(user_id),))
        yield connection
