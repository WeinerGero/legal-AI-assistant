"""Самостоятельная точка входа конкурса (ARCHITECTURE.md §13.1–13.6).

Входы: ``DATA_DIR/questions.csv`` и ``DATA_DIR/knowledge-base.zip``.
Выход: ``submission.csv`` с колонками ``id,answer``.

Использует только разрешённые библиотеки и корневые файлы конкурсного ZIP.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
from pathlib import Path
from time import perf_counter
from tempfile import TemporaryDirectory

try:
    from submission.budget import BudgetExceededError, RuntimeBudget
    from submission.reproducibility import DEFAULT_SEED, configure_reproducibility
    from submission.model_client import ModelClient
    from submission.metrics import MetricsRecorder
    from submission.pipeline import SubmissionPipeline
    from submission.recording import ReplayMismatchError
    from submission.search import HybridIndex, load_fragments
except ImportError:  # плоский корень submission.zip
    from budget import BudgetExceededError, RuntimeBudget
    from reproducibility import DEFAULT_SEED, configure_reproducibility
    from model_client import ModelClient
    from metrics import MetricsRecorder
    from pipeline import SubmissionPipeline
    from recording import ReplayMismatchError
    from search import HybridIndex, load_fragments

try:
    from submission.embedded_index import unpack
except ImportError:
    try:
        from embedded_index import unpack
    except ImportError:
        def unpack(directory: Path) -> Path:
            """Source-tree fallback; competition ZIP always uses embedded_index.py."""
            source = Path(__file__).resolve().parent / "assets" / "bge_m3"
            directory.mkdir(parents=True, exist_ok=True)
            for name in ("index.faiss", "chunks.json", "manifest.json"):
                (directory / name).write_bytes((source / name).read_bytes())
            return directory

MAX_ANSWER_CHARS = 10_000
DEFAULT_TIME_LIMIT_SECONDS = 1080.0
DEFAULT_FAILURE_ANSWER = "Недостаточно подтверждённых данных в предоставленных документах."


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Настройки из окружения (§13.5). Секреты не логируются."""

    data_dir: Path
    top_k: int = 24
    reranker_top_n: int = 8
    temperature: float = 0.0
    max_tokens: int = 1_536
    max_workers: int = 2
    time_limit_seconds: float = DEFAULT_TIME_LIMIT_SECONDS
    seed: int = DEFAULT_SEED

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
            top_k=max(24, min(40, _env_int("TOP_K", 4) * 6)),
            reranker_top_n=max(8, min(10, _env_int("RERANKER_TOP_N", 2) * 4)),
            max_tokens=max(512, min(4096, _env_int("MAX_TOKENS", 1536))),
            temperature=_env_float("TEMPERATURE", 0.0),
            max_workers=max(1, min(4, _env_int("MAX_WORKERS", 4))),
            time_limit_seconds=max(
                0.1,
                min(
                    DEFAULT_TIME_LIMIT_SECONDS,
                    _env_time_limit("TIME_LIMIT_SECONDS", DEFAULT_TIME_LIMIT_SECONDS),
                ),
            ),
        )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, min(2.0, float(os.getenv(name, ""))))
    except (ValueError, TypeError):
        return default


def _env_time_limit(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (ValueError, TypeError):
        return default


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


def write_submission_atomic(path: Path, rows: list[tuple[str, str]]) -> None:
    """Replace the complete CSV atomically, preserving a valid file on interruption."""
    temporary = path.with_name(f".{path.name}.tmp")
    write_submission(temporary, rows)
    os.replace(temporary, path)


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
    args = argv or []
    config = RuntimeConfig.from_env()
    budget = RuntimeBudget.start(config.time_limit_seconds)
    configure_reproducibility(config.seed)
    metrics_value = os.environ.get("METRICS_PATH", "").strip()
    metrics = MetricsRecorder(Path(metrics_value)) if metrics_value else None
    output_path = Path(args[0]) if args else Path("submission.csv")
    questions = read_questions(config.data_dir / "questions.csv")
    answers: list[str] = [DEFAULT_FAILURE_ANSWER] * len(questions)
    write_submission_atomic(
        output_path,
        [(question_id, answers[index]) for index, (question_id, _question) in enumerate(questions)],
    )
    knowledge_path = _find_data_file(
        config.data_dir, "kb.zip", "knowledge-base.zip", "knowledge_base.zip"
    )
    client = ModelClient(
        seed=config.seed,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        metrics=metrics,
        budget=budget,
    )
    run_config = {
        "seed": config.seed,
        "models": {"llm": client.llm_model, "embedding": client.embedding_model,
                   "reranker": client.reranker_model},
        "parameters": {"temperature": config.temperature, "max_tokens": config.max_tokens,
                       "top_k": config.top_k, "reranker_top_n": config.reranker_top_n,
                       "max_workers": config.max_workers,
                       "time_limit_seconds": config.time_limit_seconds},
    }
    client.recording.checkpoint("run.config", run_config)
    if metrics is not None:
        metrics.record_config(run_config)
    try:
        with TemporaryDirectory(prefix="psb_faiss_") as temporary:
            index_dir = Path(os.environ.get("INDEX_DIR") or temporary)
            index_started = perf_counter()
            index_mode = "bundled"
            try:
                budget.require("index load")
                if not os.environ.get("INDEX_DIR"):
                    unpack(index_dir)
                index = HybridIndex.from_prebuilt(index_dir, knowledge_path, client)
            except ReplayMismatchError:
                raise
            except Exception as exc:
                print(f"Готовый индекс недоступен: {type(exc).__name__}: {exc}", file=sys.stderr)
                index_mode = "bm25_fallback"
                index = HybridIndex.bm25_only(load_fragments(knowledge_path), client)
            if metrics is not None:
                metrics.record("rag", "index_load",
                               duration_ms=round((perf_counter() - index_started) * 1000, 3),
                               error_count=0, index_mode=index_mode,
                               fragments_count=len(index.fragments),
                               unique_sources_count=len({fragment.source for fragment in index.fragments}))
            print(f"Индекс: {index_mode}; документов: {len({item.source for item in index.fragments})}; "
                  f"фрагментов: {len(index.fragments)}; вопросов: {len(questions)}", flush=True)
            pipeline = SubmissionPipeline(index, client, top_k=config.top_k,
                                          evidence_k=config.reranker_top_n)
            concurrent = client.recording.mode == "live" and config.max_workers > 1
            def solve(position: int, question_id: str, question: str) -> tuple[int, str]:
                if concurrent:
                    worker_client = ModelClient(
                        seed=config.seed,
                        max_tokens=config.max_tokens,
                        temperature=config.temperature,
                        metrics=metrics,
                        budget=budget,
                    )
                    worker_index = copy.copy(index)
                    worker_index.client = worker_client
                    worker_pipeline = SubmissionPipeline(
                        worker_index, worker_client, top_k=config.top_k,
                        evidence_k=config.reranker_top_n
                    )
                else:
                    worker_client = client
                    worker_pipeline = pipeline
                if metrics is not None:
                    metrics.set_question(question_id)
                worker_client.recording.set_question(question_id)
                try:
                    budget.require(f"question {question_id}")
                    try:
                        answer = worker_pipeline.run(question)
                    except ReplayMismatchError:
                        raise
                    except BudgetExceededError:
                        answer = DEFAULT_FAILURE_ANSWER
                    except Exception as exc:
                        print(f"Вопрос {question_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                        try:
                            budget.require("recovery answer", reserve_seconds=15.0)
                            answer = worker_pipeline.recovery_answer(question)
                        except ReplayMismatchError:
                            raise
                        except Exception as recovery_exc:
                            print(f"Резервный ответ {question_id}: {type(recovery_exc).__name__}", file=sys.stderr)
                            answer = DEFAULT_FAILURE_ANSWER
                    return position, answer
                finally:
                    worker_client.recording.set_question(None)
                    if metrics is not None:
                        metrics.set_question(None)

            if concurrent:
                executor = ThreadPoolExecutor(max_workers=config.max_workers)
                pending = {
                    executor.submit(solve, i, qid, q)
                    for i, (qid, q) in enumerate(questions)
                }
                completed = 0
                try:
                    while pending and not budget.exhausted():
                        done, pending = wait(
                            pending,
                            timeout=min(1.0, budget.remaining()),
                            return_when=FIRST_COMPLETED,
                        )
                        for future in done:
                            position, answer = future.result()
                            answers[position] = answer
                            completed += 1
                            write_submission_atomic(
                                output_path,
                                [(qid, answers[i]) for i, (qid, _q) in enumerate(questions)],
                            )
                            if completed % 10 == 0 or completed == len(questions):
                                print(f"Обработано {completed}/{len(questions)}", flush=True)
                    for future in pending:
                        future.cancel()
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
            else:
                for i, (qid, q) in enumerate(questions):
                    if budget.exhausted():
                        break
                    position, answer = solve(i, qid, q)
                    answers[position] = answer
                    write_submission_atomic(
                        output_path,
                        [(row_id, answers[j]) for j, (row_id, _row_q) in enumerate(questions)],
                    )
                    if (i + 1) % 10 == 0 or i + 1 == len(questions):
                        print(f"Обработано {i + 1}/{len(questions)}", flush=True)
            rows = [(qid, answers[i]) for i, (qid, _question) in enumerate(questions)]
            if metrics is not None:
                metrics.set_question(None)
            client.recording.set_question(None)
            write_submission_atomic(output_path, rows)
            validate_submission(output_path, [question_id for question_id, _ in questions])
            client.recording.checkpoint("submission.csv", {"sha256": _sha256_or_none(output_path)})
            print(f"Создан submission.csv: {len(rows)} ответов", flush=True)
            if metrics is not None:
                metrics.record(
                    "budget",
                    "run",
                    duration_ms=round(budget.elapsed() * 1000, 3),
                    limit_seconds=config.time_limit_seconds,
                    elapsed_seconds=round(budget.elapsed(), 3),
                    remaining_seconds=round(budget.remaining(), 3),
                    consumed_ratio=round(
                        min(1.0, budget.elapsed() / config.time_limit_seconds), 6
                    ),
                    questions_total=len(questions),
                    completed_questions=sum(answer != DEFAULT_FAILURE_ANSWER for answer in answers),
                )
            return 0
    finally:
        try:
            client.recording.set_question(None)
            client.recording.finalize()
        finally:
            if metrics is not None:
                metrics.set_question(None)
                summary = metrics.finalize()
                print("Метрики: " + json.dumps(summary, ensure_ascii=False, default=str)[:2000],
                      file=sys.stderr)


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
