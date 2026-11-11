"""Worker-процесс: очередь заданий в PostgreSQL (ARCHITECTURE.md §1.1, §11.2).

Обычный процесс Python, читающий ``jobs`` через ``FOR UPDATE SKIP LOCKED``
с ограниченной по времени арендой. Дополнительная система очередей для базы
из 21 документа не требуется.
"""

from __future__ import annotations

from dataclasses import dataclass

LEASE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    owner: str
    max_attempts: int = 3
    lease_seconds: int = LEASE_SECONDS


def main() -> None:
    """Точка входа: получение задания, запуск конвейера, сохранение итога."""
    raise NotImplementedError("worker-цикл: получить → выполнить → записать")


if __name__ == "__main__":
    main()
