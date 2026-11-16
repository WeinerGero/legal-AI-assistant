"""Полная трасса одного вопроса для примера «как у организаторов».

Запуск (рядом с dev/run_local.ps1):

    poetry run python -m submission.dev.organizer_example --question-id 1 --output <path>.json

Скрипт ничего не меняет в конкурсном конвейере: он использует те же
``HybridIndex`` и ``SubmissionPipeline``, что и ``solution.py``, но сохраняет
промежуточные артефакты (анализ, evidence, генерацию, верификацию, финальный
ответ и тайминги по стадиям) в один JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from submission.metrics import MetricsRecorder  # noqa: E402
from submission.model_client import ModelClient  # noqa: E402
from submission.pipeline import SubmissionPipeline  # noqa: E402
from submission.search import HybridIndex  # noqa: E402

DEFAULT_ASSETS = ROOT / "submission" / "assets" / "bge_m3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--question-id", default="1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, default=None)
    args = parser.parse_args(argv)

    data_dir = args.run_dir / "data"
    questions = _read_questions(data_dir / "questions.csv")
    question = next(text for qid, text in questions if qid == args.question_id)
    knowledge = _knowledge_zip(data_dir)
    metrics_path = args.metrics or (args.output.parent / "example_metrics.jsonl")
    if metrics_path.is_file():
        metrics_path.unlink()
    metrics = MetricsRecorder(metrics_path)
    client = ModelClient(max_tokens=1536, temperature=0.3, metrics=metrics)
    example: dict[str, object] = {
        "provenance": {
            "kind": "single-question full pipeline trace",
            "question_id": args.question_id,
            "model": client.llm_model,
            "base_url": client.base_url,
            "embedding_model": client.embedding_model,
            "reranker_model": client.reranker_model,
            "embedding_base_url": client.embedding_base_url,
            "reranker_base_url": client.reranker_base_url,
            "api_mode": client.recording.mode,
            "index_dir": os.environ.get("INDEX_DIR", str(DEFAULT_ASSETS)),
        },
        "question": question,
    }

    stages: dict[str, object] = {}
    started = perf_counter()
    index_dir = Path(os.environ.get("INDEX_DIR") or DEFAULT_ASSETS)
    index = HybridIndex.from_prebuilt(index_dir, knowledge, client)
    stages["index"] = {
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "documents": len({item.source for item in index.fragments}),
        "fragments": len(index.fragments),
        "faiss_vectors": int(index.faiss.ntotal) if index.faiss is not None else 0,
    }

    pipeline = SubmissionPipeline(index, client, top_k=_env_int("TOP_K", 24), evidence_k=_env_int("RERANKER_TOP_N", 8))
    original_generate = pipeline._generate
    original_verify = pipeline._verify
    captured: dict[str, object] = {}

    def traced_generate(request: dict, evidence: list[dict]) -> dict:
        started = perf_counter()
        result = original_generate(request, evidence)
        captured.setdefault("generations", []).append(
            {
                "mode": request.get("mode", "initial"),
                "request": request,
                "output": result,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            }
        )
        return result

    def traced_verify(messages, analysis, generation, evidence) -> dict:
        started = perf_counter()
        result = original_verify(messages, analysis, generation, evidence)
        captured.setdefault("verifications", []).append(
            {"output": result, "duration_ms": round((perf_counter() - started) * 1000, 3)}
        )
        return result

    pipeline._generate = traced_generate  # type: ignore[method-assign]
    pipeline._verify = traced_verify  # type: ignore[method-assign]

    started = perf_counter()
    try:
        answer = pipeline.run(question)
        example["error"] = None
    except Exception as exc:  # noqa: BLE001 - трасса должна сохранить причину отказа
        answer = None
        example["error"] = {"type": type(exc).__name__, "message": str(exc)}
    stages["pipeline"] = round((perf_counter() - started) * 1000, 3)
    example["stages_ms"] = stages
    example["outcome"] = dict(pipeline.last_outcome)
    example["answer"] = answer
    example["trace"] = captured
    example["question_metrics"] = _question_metrics(metrics_path, args.question_id)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {args.output}")
    return 0


def _question_metrics(path: Path, question_id: str) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("question_id")) == str(question_id):
            rows.append(row)
    return rows


def _read_questions(path: Path) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [(row["id"].strip(), row["question"].strip()) for row in reader]


def _knowledge_zip(data_dir: Path) -> Path:
    for name in ("kb.zip", "knowledge-base.zip", "knowledge_base.zip"):
        candidate = data_dir / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"knowledge base not found in {data_dir}")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
