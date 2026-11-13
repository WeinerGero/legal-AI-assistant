"""Самостоятельная точка входа конкурса (ARCHITECTURE.md §13.1–13.6).

Входы: ``DATA_DIR/questions.csv`` и ``DATA_DIR/knowledge-base.zip``.
Выход: ``submission.csv`` с колонками ``id,answer``.

Использует только разрешённые библиотеки и корневые файлы конкурсного ZIP.
"""

from __future__ import annotations

import csv
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

try:
    from core.reproducibility import DEFAULT_SEED, configure_reproducibility
    from submission.model_client import ModelClient
    from submission.metrics import MetricsRecorder
    from submission.pipeline import SubmissionPipeline
    from submission.recording import ReplayMismatchError
    from submission.search import HybridIndex
except ImportError:  # плоский корень submission.zip
    from reproducibility import DEFAULT_SEED, configure_reproducibility
    from model_client import ModelClient
    from metrics import MetricsRecorder
    from pipeline import SubmissionPipeline
    from recording import ReplayMismatchError
    from search import HybridIndex

MAX_ANSWER_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Настройки из окружения (§13.5). Секреты не логируются."""

    data_dir: Path
    top_k: int = 4
    reranker_top_n: int = 2
    temperature: float = 0.0
    max_tokens: int = 1_024
    max_workers: int = 2
    seed: int = DEFAULT_SEED

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
        )


def read_questions(path: Path) -> list[tuple[str, str]]:
    """Читает ``questions.csv``; каждая строка обрабатывается изолированно."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
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
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
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
    metrics_value = os.environ.get("METRICS_PATH", "").strip()
    metrics = MetricsRecorder(Path(metrics_value)) if metrics_value else None
    questions_path = config.data_dir / "questions.csv"
    knowledge_path = _find_data_file(
        config.data_dir,
        "kb.zip",
        "knowledge-base.zip",
        "knowledge_base.zip",
    )
    output_path = Path(args[0]) if args else Path("submission.csv")
    module_dir = Path(__file__).resolve().parent
    bundled_index_dir = (
        module_dir
        if (module_dir / "index.faiss").is_file()
        else module_dir / "assets" / "bge_m3"
    )
    index_dir = Path(os.environ.get("INDEX_DIR", bundled_index_dir))
    client = ModelClient(seed=config.seed, max_tokens=config.max_tokens, metrics=metrics)
    run_config = {
        "seed": config.seed,
        "models": {
            "llm": client.llm_model,
            "embedding": client.embedding_model,
            "reranker": client.reranker_model,
        },
        "parameters": {
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "top_k": config.top_k,
            "reranker_top_n": config.reranker_top_n,
        },
        "checksums": {
            name: _sha256_or_none(index_dir / name)
            for name in ("index.faiss", "chunks.json", "manifest.json")
        },
    }
    client.recording.checkpoint("run.config", run_config)
    if metrics is not None:
        metrics.record_config(run_config)
    try:
        questions = read_questions(questions_path)
        index_started = perf_counter()
        try:
            index = HybridIndex.from_prebuilt(index_dir, knowledge_path, client)
        except Exception as exc:
            if metrics is not None:
                metrics.record(
                    "rag",
                    "index_load",
                    duration_ms=round((perf_counter() - index_started) * 1000, 3),
                    error_count=1,
                    error_type=type(exc).__name__,
                )
            raise
        if metrics is not None:
            metrics.record(
                "rag",
                "index_load",
                duration_ms=round((perf_counter() - index_started) * 1000, 3),
                error_count=0,
                fragments_count=len(index.fragments),
                unique_sources_count=len({fragment.source for fragment in index.fragments}),
            )
        pipeline = SubmissionPipeline(
            index,
            client,
            top_k=config.top_k,
            evidence_k=config.reranker_top_n,
        )
        rows: list[tuple[str, str]] = []
        for question_id, question in questions:
            if metrics is not None:
                metrics.set_question(question_id)
            client.recording.set_question(question_id)
            try:
                answer = pipeline.run(question)
            except ReplayMismatchError:
                raise
            except Exception as exc:
                print(f"question {question_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                answer = "Не удалось получить подтверждённый ответ из-за технической ошибки."
            rows.append((question_id, answer))
        if metrics is not None:
            metrics.set_question(None)
        client.recording.set_question(None)
        write_submission(output_path, rows)
        validate_submission(output_path, [question_id for question_id, _question in questions])
        client.recording.checkpoint(
            "submission.csv", {"sha256": _sha256_or_none(output_path)}
        )
        return 0
    finally:
        try:
            client.recording.set_question(None)
            client.recording.finalize()
        finally:
            if metrics is not None:
                metrics.set_question(None)
                metrics.finalize()


def _sha256_or_none(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_data_file(data_dir: Path, *names: str) -> Path:
    for root in (data_dir, Path(__file__).resolve().parent / data_dir, Path.cwd()):
        for name in names:
            path = root / name
            if path.is_file():
                return path
    raise FileNotFoundError(f"не найден один из файлов: {', '.join(names)}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
