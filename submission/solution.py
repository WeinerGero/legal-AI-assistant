"""Самостоятельная точка входа конкурса (ARCHITECTURE.md §13.1–13.6).

Входы: ``DATA_DIR/questions.csv`` и ``DATA_DIR/knowledge-base.zip``.
Выход: ``submission.csv`` с колонками ``id,answer``.

Использует только разрешённые библиотеки и корневые файлы конкурсного ZIP.
"""

from __future__ import annotations

import csv
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_ANSWER_CHARS = 10_000
TECHNICAL_FAILURE_ANSWER = (
    "Не удалось сформировать подтверждённый ответ из-за технической ошибки. "
    "Юридические сведения не были выдуманы."
)


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
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"id", "question"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError("questions.csv должен содержать колонки id и question")
        rows: list[tuple[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            question_id = (row.get("id") or "").strip()
            question = (row.get("question") or "").strip()
            if not question_id or not question:
                raise ValueError(f"пустые id/question в строке {line_number}")
            rows.append((question_id, question))
        return rows


def write_submission(path: Path, rows: list[tuple[str, str]]) -> None:
    """Пишет ``submission.csv`` ровно с колонками ``id,answer``."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "answer"])
        for question_id, answer in rows:
            writer.writerow([question_id, answer[:MAX_ANSWER_CHARS]])


def load_answerer(spec: str | None = None) -> Callable[[str, RuntimeConfig], str]:
    """Загружает конкурсный адаптер ``module:function`` без связи ядра с транспортом."""
    target = spec or os.environ.get("LEGAL_ASSISTANT_FACTORY", "")
    if not target or ":" not in target:
        raise RuntimeError("не задан LEGAL_ASSISTANT_FACTORY в формате module:function")
    module_name, function_name = target.split(":", 1)
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"конкурсная фабрика не вызываема: {target}")
    return function


def main(argv: list[str] | None = None) -> int:
    """Конвейер целиком; при техническом сбое юридический ответ не изобретается."""
    arguments = list(argv or ())
    config = RuntimeConfig.from_env()
    questions_path = (
        Path(arguments[0]) if arguments else config.data_dir / "questions.csv"
    )
    output_path = Path(arguments[1]) if len(arguments) > 1 else Path("submission.csv")
    questions = read_questions(questions_path)
    try:
        answerer = load_answerer()
    except Exception:
        answerer = None

    rows: list[tuple[str, str]] = []
    for question_id, question in questions:
        answer = TECHNICAL_FAILURE_ANSWER
        if answerer is not None:
            try:
                candidate = str(answerer(question, config)).strip()
                if candidate:
                    answer = candidate
            except Exception:
                answer = TECHNICAL_FAILURE_ANSWER
        rows.append((question_id, answer))
    write_submission(output_path, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
