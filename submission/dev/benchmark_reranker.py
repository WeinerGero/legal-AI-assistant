from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    chunks = json.loads(
        (ROOT / "submission" / "assets" / "bge_m3" / "chunks.json").read_text(encoding="utf-8-sig")
    )
    rows = chunks.get("chunks", []) if isinstance(chunks, dict) else chunks
    documents = [str(row.get("text") or row.get("search_text") or "")[:1200] for row in rows[:24]]
    model = httpx.get(f"{args.base_url}/v1/models", timeout=10.0).json()["data"][0]["id"]
    query = "Определить применимое правило, исключения, дополнительные обязанности и срок"
    cases = [(24, 1), (24, 2), (24, 4), (16, 2), (12, 2)]
    results = [run_case(args.base_url, model, query, documents[:count], concurrency) for count, concurrency in cases]
    payload = {
        "model": model,
        "server_slots": len(httpx.get(f"{args.base_url}/slots", timeout=10.0).json()),
        "candidate_set": "fixed first 24 bundled-index chunks; identical within every concurrency comparison",
        "quality_labels": "N/A: benchmark candidates have no independent relevance labels",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_case(base_url: str, model: str, query: str, documents: list[str], concurrency: int) -> dict[str, Any]:
    stop = threading.Event()
    samples: list[int] = []

    def sample_slots() -> None:
        while not stop.wait(0.05):
            try:
                slots = httpx.get(f"{base_url}/slots", timeout=2.0).json()
                samples.append(sum(bool(slot.get("is_processing")) for slot in slots))
            except Exception:
                continue

    sampler = threading.Thread(target=sample_slots, daemon=True)
    sampler.start()

    def request() -> tuple[float, int]:
        started = time.perf_counter()
        response = httpx.post(
            f"{base_url}/v1/rerank",
            json={"model": model, "query": query, "documents": documents, "top_n": min(8, len(documents))},
            timeout=300.0,
        )
        response.raise_for_status()
        return (time.perf_counter() - started) * 1000, len(response.json().get("results", []))

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        calls = list(executor.map(lambda _index: request(), range(concurrency)))
    wall_ms = (time.perf_counter() - wall_started) * 1000
    stop.set()
    sampler.join(timeout=1.0)
    durations = [item[0] for item in calls]
    max_busy = max(samples, default=0)
    return {
        "candidates": len(documents),
        "active_requests": concurrency,
        "wall_ms": round(wall_ms, 3),
        "request_durations_ms": [round(value, 3) for value in durations],
        "throughput_requests_per_second": round(concurrency / (wall_ms / 1000), 6),
        "max_busy_slots_observed": max_busy,
        "max_queued_requests_estimate": max(0, concurrency - max_busy),
        "returned_per_request": [item[1] for item in calls],
        "recall_at_k": "N/A: no independent relevance labels",
        "ndcg_at_k": "N/A: no independent relevance labels",
        "evidence_completeness": "N/A: no fact-to-fragment labels",
    }


if __name__ == "__main__":
    raise SystemExit(main())
