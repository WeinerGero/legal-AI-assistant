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

try:
    from core.reproducibility import DEFAULT_SEED, configure_reproducibility
    from submission.model_client import ModelClient
    from submission.pipeline import SubmissionPipeline
    from submission.search import HybridIndex, load_fragments
except ImportError:  # плоский корень submission.zip
    from reproducibility import DEFAULT_SEED, configure_reproducibility
    from model_client import ModelClient
    from pipeline import SubmissionPipeline
    from search import HybridIndex, load_fragments

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
    seed: int = DEFAULT_SEED

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", ".")),
            top_k=int(os.environ.get("TOP_K", "30")),
            reranker_top_n=int(os.environ.get("RERANKER_TOP_N", "10")),
            temperature=float(os.environ.get("TEMPERATURE", "0.0")),
            max_tokens=int(os.environ.get("MAX_TOKENS", "1500")),
            max_workers=int(os.environ.get("MAX_WORKERS", "4")),
            seed=int(os.environ.get("SEED", str(DEFAULT_SEED))),
        )


def read_questions(path: Path) -> list[tuple[str, str]]:
    """Читает ``questions.csv``; каждая строка обрабатывается изолированно."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["id", "question"]:
            raise ValueError("questions.csv должен содержать ровно колонки id,question")
        rows = [(row["id"].strip(), row["question"].strip()) for row in reader]
    if not rows:
        raise ValueError("questions.csv не содержит вопросов")
    ids = [question_id for question_id, _question in rows]
    if any(not question_id or not question for question_id, question in rows):
        raise ValueError("id и question не могут быть пустыми")
    if len(ids) != len(set(ids)):
        raise ValueError("questions.csv содержит повторяющиеся id")
    return rows


def write_submission(path: Path, rows: list[tuple[str, str]]) -> None:
    """Пишет ``submission.csv`` ровно с колонками ``id,answer``."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "answer"])
        for question_id, answer in rows:
            normalized = answer.strip()
            if not question_id or not normalized:
                raise ValueError("submission не допускает пустые id или answer")
            writer.writerow([question_id, normalized[:MAX_ANSWER_CHARS]])


def validate_submission(path: Path, expected_ids: list[str]) -> None:
    """Проверяет контракт результата после фактической записи CSV."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["id", "answer"]:
            raise ValueError("submission.csv должен содержать ровно колонки id,answer")
        rows = list(reader)
    if [row["id"] for row in rows] != expected_ids:
        raise ValueError("порядок или состав id в submission.csv не совпадает с questions.csv")
    if any(not row["answer"].strip() or len(row["answer"]) > MAX_ANSWER_CHARS for row in rows):
        raise ValueError("submission.csv содержит пустой или слишком длинный answer")


def main(argv: list[str] | None = None) -> int:
    """Конвейер целиком; при техническом сбое юридический ответ не изобретается."""
    args = argv or []
    config = RuntimeConfig.from_env()
    configure_reproducibility(config.seed)
    questions_path = config.data_dir / "questions.csv"
    knowledge_path = config.data_dir / "knowledge-base.zip"
    output_path = Path(args[0]) if args else Path("submission.csv")

    questions = read_questions(questions_path)
    client = ModelClient(seed=config.seed)
    index_dir = Path(os.environ.get("INDEX_DIR", config.data_dir / "index" / "bge_m3"))
    if (index_dir / "index.faiss").is_file():
        index = HybridIndex.from_prebuilt(index_dir, knowledge_path, client)
    else:
        index = HybridIndex(load_fragments(knowledge_path), client)
    pipeline = SubmissionPipeline(
        index,
        client,
        top_k=config.top_k,
        evidence_k=config.reranker_top_n,
    )
    rows: list[tuple[str, str]] = []
    for question_id, question in questions:
        try:
            answer = pipeline.run(question)
        except Exception as exc:
            print(f"question {question_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            answer = "Не удалось получить подтверждённый ответ из-за технической ошибки."
        rows.append((question_id, answer))
    write_submission(output_path, rows)
    validate_submission(output_path, [question_id for question_id, _question in questions])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
