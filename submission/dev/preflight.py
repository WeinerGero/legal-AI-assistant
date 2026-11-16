"""Strict DEV_LOCAL preflight and real smoke/evaluation launchers."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import faiss
import numpy as np

from submission.evaluate_quality import evaluate, render_markdown
from submission.model_client import ModelClient, _load_local_settings

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "submission" / "assets" / "bge_m3"
ORGANIZER_DATA = ROOT / "data" / "organizers"
RUNS = ROOT / "submission" / "evaluation_runs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "smoke", "evaluate"))
    args = parser.parse_args(argv)
    report = run_preflight()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.command != "preflight":
        run_pipeline(args.command)
    return 0


def run_preflight() -> dict[str, Any]:
    _require_local_environment()
    client = ModelClient(timeout=60.0, max_tokens=256, temperature=0.3)
    local = _load_local_settings()
    expected_key = os.environ.get("LLM_DEV") or local.get("LLM_DEV")
    if not expected_key or client.api_key != expected_key:
        raise RuntimeError("DEV_LOCAL DeepSeek must use configured LLM_DEV")

    started = perf_counter()
    deepseek_raw = client.complete_json(
        "Return only valid JSON.",
        "Return a JSON object with key ok set to true.",
        stage="dev_preflight",
    )
    deepseek_payload = json.loads(deepseek_raw)
    if deepseek_payload.get("ok") is not True:
        raise RuntimeError("DeepSeek preflight returned unexpected JSON")
    deepseek_ms = round((perf_counter() - started) * 1000, 3)

    started = perf_counter()
    vectors = client.embed(["Каков размер арендной платы по договору А-08/2024?"])
    embedding_ms = round((perf_counter() - started) * 1000, 3)
    vector = np.asarray(vectors[0], dtype="float32")
    norm = float(np.linalg.norm(vector))

    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    index = faiss.read_index(str(ASSETS / "index.faiss"))
    chunks_payload = json.loads((ASSETS / "chunks.json").read_text(encoding="utf-8"))
    chunks = chunks_payload.get("chunks", []) if isinstance(chunks_payload, dict) else chunks_payload
    _validate_embedding_contract(client.embedding_model, vector, norm, manifest, index, chunks)
    negative_checks: dict[str, bool] = {}
    for name, invalid_model, invalid_vector in (
        ("unknown_model_rejected", "unknown/not-bge-m3", vector),
        ("wrong_dimension_rejected", client.embedding_model, vector[:-1]),
    ):
        try:
            _validate_embedding_contract(
                invalid_model,
                invalid_vector,
                float(np.linalg.norm(invalid_vector)),
                manifest,
                index,
                chunks,
            )
        except RuntimeError:
            negative_checks[name] = True
        else:
            raise RuntimeError(f"negative embedding contract check failed: {name}")
    normalized = vector.reshape(1, -1).copy()
    faiss.normalize_L2(normalized)
    distances, ids = index.search(normalized, 24)
    faiss_ids = [int(value) for value in ids[0] if value >= 0]
    if not faiss_ids:
        raise RuntimeError("FAISS returned no candidates for a real BGE-M3 query vector")
    expected_fragment = "А-08-2024"
    if not any(expected_fragment in chunks[item]["document"] for item in faiss_ids):
        raise RuntimeError("BGE-M3/FAISS compatibility check missed the expected contract in top 24")

    started = perf_counter()
    reranked = client.rerank(
        "Каков срок оплаты?",
        ["Оплата производится до 5-го числа.", "Поставка осуществляется партиями."],
        2,
    )
    rerank_ms = round((perf_counter() - started) * 1000, 3)
    if len(reranked) != 2 or {row[0] for row in reranked} != {0, 1}:
        raise RuntimeError("Qwen3 returned invalid candidate indexes")
    if any(not isinstance(score, (int, float)) for _index, score in reranked):
        raise RuntimeError("Qwen3 did not return relevance scores")

    return {
        "mode": "DEV_LOCAL",
        "deepseek": {
            "model": client.llm_model,
            "base_url": client.base_url,
            "authorized_json": True,
            "duration_ms": deepseek_ms,
        },
        "bge_m3": {
            "model": client.embedding_model,
            "base_url": client.embedding_base_url,
            "dimension": int(vector.size),
            "norm": norm,
            "duration_ms": embedding_ms,
        },
        "faiss": {
            "vectors": int(index.ntotal),
            "dimension": int(index.d),
            "top24_count": len(faiss_ids),
            "top_score": float(distances[0][0]),
            "expected_document_found": True,
            "negative_contract_checks": negative_checks,
        },
        "qwen3": {
            "model": client.reranker_model,
            "base_url": client.reranker_base_url,
            "duration_ms": rerank_ms,
            "results": [
                {"index": index_value, "relevance_score": score}
                for index_value, score in reranked
            ],
        },
    }


def run_pipeline(command: str) -> Path:
    run_id = datetime.now(timezone.utc).strftime(f"{command}-%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=False)
    selected_ids = {"1", "5"} if command == "smoke" else None
    _write_questions(data_dir / "questions.csv", selected_ids)
    _write_knowledge_zip(data_dir / "knowledge-base.zip")

    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "METRICS_PATH": str(run_dir / "metrics.jsonl"),
            "API_MODE": "live",
        }
    )
    output = run_dir / "submission.csv"
    completed = subprocess.run(
        [sys.executable, "-m", "submission.solution", str(output)],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{command} pipeline failed with exit code {completed.returncode}")
    expected_count = 2 if selected_ids else 18
    with output.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != expected_count or any(not row.get("answer", "").strip() for row in rows):
        raise RuntimeError("pipeline output is incomplete")

    if command == "evaluate":
        quality = evaluate(
            ORGANIZER_DATA,
            ASSETS,
            ROOT / "submission" / "evaluation_annotations.json",
            output,
        )
        (run_dir / "quality.json").write_text(
            json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "quality.md").write_text(render_markdown(quality), encoding="utf-8")
    print(f"DEV_LOCAL {command} completed: {run_dir}")
    return run_dir


def _require_local_environment() -> None:
    expected = {
        "RAG_RUNTIME_MODE": "DEV_LOCAL",
        "LLM_BASE_URL": "https://matreshkaapi.mooo.com/v1",
        "LLM_MODEL": "deepseek-v4.1-flash",
        "EMBEDDER_BASE_URL": "http://127.0.0.1:8081/v1",
        "EMB_MODEL": "BAAI/bge-m3",
        "RERANKER_BASE_URL": "http://127.0.0.1:8080/v1",
    }
    mismatched = {
        key: os.environ.get(key)
        for key, value in expected.items()
        if os.environ.get(key) != value
    }
    if mismatched:
        raise RuntimeError(f"invalid DEV_LOCAL environment variables: {sorted(mismatched)}")


def _validate_embedding_contract(
    model: str,
    vector: np.ndarray,
    norm: float,
    manifest: dict[str, Any],
    index: Any,
    chunks: list[dict[str, Any]],
) -> None:
    def canonical(value: str) -> str:
        return "".join(char for char in value.casefold() if char.isalnum())

    if canonical(model) != canonical(str(manifest["model"])):
        raise RuntimeError("BGE-M3 model identifier does not match manifest")
    if vector.ndim != 1 or vector.size != manifest["embedding_dimension"] or vector.size != index.d:
        raise RuntimeError("BGE-M3 vector dimension does not match FAISS/manifest")
    if manifest.get("normalized") is not True or not 0.99 <= norm <= 1.01:
        raise RuntimeError("BGE-M3 vector normalization does not match manifest")
    if index.ntotal != manifest["chunks_count"] or index.ntotal != len(chunks):
        raise RuntimeError("FAISS vector count does not match manifest/chunks")


def _write_questions(path: Path, selected_ids: set[str] | None) -> None:
    with (ORGANIZER_DATA / "demo_questions.csv").open(
        encoding="utf-8-sig", newline=""
    ) as source:
        rows = list(csv.DictReader(source))
    if selected_ids is not None:
        rows = [row for row in rows if row["id"] in selected_ids]
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["id", "question"])
        writer.writeheader()
        writer.writerows(rows)


def _write_knowledge_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted((ORGANIZER_DATA / "knowledge-base").glob("*")):
            if source.is_file():
                archive.write(source, source.name)


if __name__ == "__main__":
    raise SystemExit(main())
