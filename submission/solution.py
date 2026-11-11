"""Самостоятельная точка входа конкурса (ARCHITECTURE.md §13.1–13.6).

Входы: ``DATA_DIR/questions.csv`` и ``DATA_DIR/knowledge-base.zip``.
Выход: ``submission.csv`` с колонками ``id,answer``.

Использует только разрешённые библиотеки и корневые файлы конкурсного ZIP.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_ANSWER_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Настройки из окружения (§13.5). Секреты не логируются."""

    data_dir: Path
    top_k: int = 30
    reranker_top_n: int = 10
    temperature: float = 0.0
    max_tokens: int = 1_500
    max_workers: int = 4

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", ".")),
            top_k=int(os.environ.get("TOP_K", "30")),
            reranker_top_n=int(os.environ.get("RERANKER_TOP_N", "10")),
            temperature=float(os.environ.get("TEMPERATURE", "0.0")),
            max_tokens=int(os.environ.get("MAX_TOKENS", "1500")),
            max_workers=int(os.environ.get("MAX_WORKERS", "4")),
        )


def read_questions(path: Path) -> list[tuple[str, str]]:
    """Читает ``questions.csv``; каждая строка обрабатывается изолированно."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [(row["id"], row["question"]) for row in reader]


def write_submission(path: Path, rows: list[tuple[str, str]]) -> None:
    """Пишет ``submission.csv`` ровно с колонками ``id,answer``."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "answer"])
        for question_id, answer in rows:
            writer.writerow([question_id, answer[:MAX_ANSWER_CHARS]])


def main(argv: list[str] | None = None) -> int:
    """Конвейер целиком; при техническом сбое юридический ответ не изобретается."""
    raise NotImplementedError("сборка конкурсных адаптеров и запуск pipeline")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
