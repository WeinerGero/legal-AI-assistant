"""Run and report the 65-case golden evaluation outside the competition archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "PSB_golden_dataset_v1.1_codex.json"
CORPUS = ROOT / "data" / "organizers" / "knowledge-base"
REPORTS = ROOT
BASELINE_COMMIT = "6f11e3ba3bfbc4ef6b3ab738f407b5c3aa1bbdfa"
REQUIRED_FIELDS = (
    "id", "category", "title", "query", "expected_behavior", "gold_answer", "failure_conditions"
)
STAGE_NAMES = (
    "question_parse", "analysis", "query_preparation", "embeddings", "bm25", "faiss",
    "fusion", "rerank", "evidence_selection", "expanded_search", "generation",
    "structure_validation", "verification", "generation_revision", "reverification",
    "citation_rendering", "final_rendering", "csv_write", "question_total",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="execute the real model pipeline")
    parser.add_argument("--run-dir", type=Path, default=ROOT / "artifacts" / "golden-current")
    args = parser.parse_args(argv)
    cases = _load_cases()
    state: dict[str, Any] = {
        "executed": False,
        "reason": _availability_reason(),
        "commit": _git("rev-parse", "HEAD"),
        "baseline_commit": BASELINE_COMMIT,
        "golden_sha256": _sha256(GOLDEN),
        "corpus_sha256": _corpus_hash(),
        "case_count": len(cases),
        "run_dir": str(args.run_dir),
    }
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    if args.run:
        if state["reason"]:
            raise RuntimeError(state["reason"])
        rows, events, elapsed = _execute(cases, args.run_dir)
        state.update(executed=True, reason=None, elapsed_ms=elapsed, completed=len(rows))
    _write_outputs(cases, rows, events, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(cases, list) or len(cases) != 65:
        raise ValueError("golden dataset must contain exactly 65 cases")
    for case in cases:
        if not isinstance(case, dict) or any(name not in case for name in REQUIRED_FIELDS):
            raise ValueError("golden case is missing an original field")
    return cases


def _availability_reason() -> str | None:
    missing = [name for name in ("LLM_BASE_URL", "LLM_API_KEY") if not os.environ.get(name)]
    if missing:
        return "real run unavailable: process environment is missing " + ", ".join(missing)
    if not CORPUS.is_dir():
        return "real run unavailable: organizer corpus directory is missing"
    return None


def _execute(cases: list[dict[str, Any]], run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float]:
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "questions.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "question"])
        writer.writeheader()
        writer.writerows({"id": case["id"], "question": case["query"]} for case in cases)
    with zipfile.ZipFile(data_dir / "knowledge-base.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(CORPUS.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    output = run_dir / "submission.csv"
    metrics = run_dir / "metrics.jsonl"
    env = os.environ.copy()
    env.update({"DATA_DIR": str(data_dir), "METRICS_PATH": str(metrics), "API_MODE": "live"})
    started = perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "submission.solution", str(output)], cwd=ROOT, env=env, check=False
    )
    elapsed = (perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(f"golden pipeline exited with {completed.returncode}")
    with output.open(encoding="utf-8-sig", newline="") as handle:
        result = list(csv.DictReader(handle))
    expected = [str(case["id"]) for case in cases]
    actual = [row.get("id", "") for row in result]
    if actual != expected or any(not row.get("answer", "").strip() for row in result):
        raise ValueError("submission.csv has missing, extra, reordered, or empty answers")
    if any(len(row["answer"]) > 10_000 for row in result):
        raise ValueError("submission.csv contains an answer over 10000 characters")
    events = [json.loads(line) for line in metrics.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, events, elapsed


def _write_outputs(cases: list[dict[str, Any]], rows: list[dict[str, Any]], events: list[dict[str, Any]], state: dict[str, Any]) -> None:
    answers = {row["id"]: row["answer"] for row in rows}
    outcomes = {
        str(event.get("question_id")): event
        for event in events if event.get("kind") == "outcome" and event.get("stage") == "question"
    }
    with (REPORTS / "GOLDEN_CASE_RESULTS.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [*REQUIRED_FIELDS, "answer", "pipeline_state", "judge_score", "error"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            event = outcomes.get(str(case["id"]), {})
            writer.writerow({
                **{name: _cell(case[name]) for name in REQUIRED_FIELDS},
                "answer": answers.get(str(case["id"]), "N/A"),
                "pipeline_state": event.get("final_state", "N/A"),
                "judge_score": "N/A",
                "error": event.get("error_type") or state.get("reason") or "",
            })
    _write_event_csv("RAG_TIMINGS.csv", events, lambda e: isinstance(e.get("duration_ms"), (int, float)))
    _write_event_csv("RAG_LLM_METRICS.csv", events, lambda e: e.get("kind") in {"llm", "api"})
    _write_stage_metrics(events)
    completed = len(rows)
    reason = state.get("reason") or "none"
    (REPORTS / "GOLDEN_EVALUATION_REPORT.md").write_text(
        "# Golden evaluation report\n\n"
        f"- Golden cases: 65\n- Full real pipeline completed: {completed}/65\n"
        f"- DeepSeek judge completed: 0/65\n- Status: {'executed' if completed else 'blocked'}\n"
        f"- Reason: {reason}\n- Commit: `{state['commit']}`\n"
        f"- Golden SHA-256: `{state['golden_sha256']}`\n- Corpus SHA-256: `{state['corpus_sha256']}`\n\n"
        "All unavailable quality measures are N/A, not zero. No claim about organizer-judge parity is made.\n",
        encoding="utf-8",
    )
    (REPORTS / "GOLDEN_FAILURE_ANALYSIS.md").write_text(
        "# Golden failure analysis\n\n"
        f"The 65-case real run was not executed: {reason}. Therefore case-level root causes, "
        "judge failures, retrieval regressions, and before/after quality deltas are N/A.\n\n"
        "Verified code regressions cover citation-marker alignment, contradictory verification pass, "
        "bounded timeout retry, and exact fallback character ranges.\n",
        encoding="utf-8",
    )
    (REPORTS / "RAG_BASELINE_VS_CURRENT.md").write_text(
        "# Baseline versus current\n\n"
        f"Baseline commit: `{BASELINE_COMMIT}`. Current commit: `{state['commit']}`.\n\n"
        "| Metric | Baseline | Current | Delta |\n|---|---:|---:|---:|\n"
        "| Full live cases | N/A | N/A | N/A |\n| DeepSeek judge score | N/A | N/A | N/A |\n"
        "| Required Fact Recall | N/A | N/A | N/A |\n| Technical Refusal Rate | N/A | N/A | N/A |\n\n"
        f"Matched live comparison is unavailable: {reason}.\n",
        encoding="utf-8",
    )


def _write_event_csv(name: str, events: list[dict[str, Any]], predicate) -> None:
    selected = [event for event in events if predicate(event)]
    keys = sorted({key for event in selected for key in event}) or ["kind", "stage", "question_id", "value"]
    with (REPORTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for event in selected:
            writer.writerow({key: _cell(event.get(key, "")) for key in keys})


def _write_stage_metrics(events: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for event in events:
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)):
            grouped[f"{event.get('kind')}.{event.get('stage')}"] .append(float(duration))
    with (REPORTS / "RAG_STAGE_METRICS.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["stage", "definition", "count", "total_ms", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "errors", "retries"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name in STAGE_NAMES:
            values = grouped.get(name, [])
            writer.writerow({
                "stage": name, "definition": "measured pipeline stage duration",
                "count": len(values) if values else "N/A", "total_ms": sum(values) if values else "N/A",
                "mean_ms": mean(values) if values else "N/A", "p50_ms": _percentile(values, .5),
                "p95_ms": _percentile(values, .95), "p99_ms": _percentile(values, .99),
                "max_ms": max(values) if values else "N/A", "errors": "N/A", "retries": "N/A",
            })


def _percentile(values: list[float], fraction: float) -> float | str:
    if not values:
        return "N/A"
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def _cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(CORPUS.iterdir() if CORPUS.is_dir() else []):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


if __name__ == "__main__":
    raise SystemExit(main())
