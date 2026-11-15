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

import httpx

from submission.model_client import _load_local_settings
from submission.evaluate_quality import labeled_ranking_metrics

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "PSB_golden_dataset_v1.1_codex.json"
CORPUS = ROOT / "data" / "organizers" / "knowledge-base"
REPORTS = ROOT
PARTITIONS_PATH = ROOT / "tests" / "golden" / "golden_partitions.json"
PARTITION_RUNS = ROOT / "artifacts" / "golden-runs"
P03_RELEVANCE = ROOT / "tests" / "golden" / "p03_relevance_v1.json"
INDEX_CHUNKS = ROOT / "submission" / "assets" / "bge_m3" / "chunks.json"
HISTORY_RUNS = ROOT / "tests" / "golden" / "history_runs.json"
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
    parser.add_argument("--partition", choices=[f"P{index:02d}" for index in range(1, 14)])
    parser.add_argument("--run-id")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--recalculate", action="store_true")
    parser.add_argument("--dev-local", action="store_true", help="use the existing DEV_LOCAL services")
    args = parser.parse_args(argv)
    cases = _load_cases()
    manifest = _load_partition_manifest(cases)
    run_id = args.run_id or f"local-{_git('rev-parse', '--short=7', 'HEAD')}"
    run_root = PARTITION_RUNS / run_id
    if args.status or (not args.partition and not args.recalculate):
        _print_partition_status(manifest, run_root)
        return 0
    if args.dev_local:
        _configure_dev_local()
    if args.partition:
        reason = _availability_reason()
        if reason:
            raise RuntimeError(reason)
        _run_partition(args.partition, manifest, cases, run_root)
    _recalculate_partition_run(manifest, cases, run_root)
    _print_partition_status(manifest, run_root)
    return 0


def _load_partition_manifest(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not PARTITIONS_PATH.is_file():
        raise FileNotFoundError(f"partition manifest is missing: {PARTITIONS_PATH}")
    manifest = json.loads(PARTITIONS_PATH.read_text(encoding="utf-8-sig"))
    partitions = manifest.get("partitions") if isinstance(manifest, dict) else None
    if manifest.get("dataset_sha256") != _sha256(GOLDEN) or not isinstance(partitions, list) or len(partitions) != 13:
        raise ValueError("partition manifest does not match the golden dataset")
    ids = [str(case["id"]) for case in cases]
    assigned = [str(case_id) for part in partitions for case_id in part.get("case_ids", [])]
    if len(assigned) != 65 or len(set(assigned)) != 65 or set(assigned) != set(ids):
        raise ValueError("partition manifest must assign every case exactly once")
    if any(part.get("partition_size") != 5 or len(part.get("case_ids", [])) != 5 for part in partitions):
        raise ValueError("each partition must contain exactly five cases")
    return manifest


def _run_partition(
    partition_id: str,
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
    run_root: Path,
) -> None:
    part = next(item for item in manifest["partitions"] if item["partition_id"] == partition_id)
    partition_dir = run_root / "partitions" / partition_id
    state_path = partition_dir / "state.json"
    state = _read_json(state_path, {"partition_id": partition_id, "cases": {}, "status": "pending", "attempts": 0})
    completed = {case_id for case_id, row in state.get("cases", {}).items() if row.get("status") == "complete"}
    selected = [case for case in cases if str(case["id"]) in part["case_ids"] and str(case["id"]) not in completed]
    if not selected:
        return
    run_root.mkdir(parents=True, exist_ok=True)
    _ensure_run_metadata(run_root, manifest)
    existing_rows = _read_answer_rows(partition_dir / "answers.csv")
    for case in selected:
        case_id = str(case["id"])
        attempt_number = int(state.get("attempts", 0)) + 1
        attempt_dir = partition_dir / "attempts" / f"A{attempt_number:02d}-{case_id}"
        state.update(status="running", attempts=attempt_number, active_case_id=case_id)
        _atomic_json(state_path, state)
        try:
            rows, events, elapsed = _execute([case], attempt_dir)
            outcomes = {
                str(event.get("question_id")): event
                for event in events
                if event.get("kind") == "outcome" and event.get("stage") == "question"
            }
            if set(outcomes) != {case_id} or len(rows) != 1 or str(rows[0]["id"]) != case_id:
                raise RuntimeError("case attempt has no single matching terminal outcome")
        except Exception:
            state.update(status="interrupted", active_case_id=case_id)
            _atomic_json(state_path, state)
            raise
        if case_id in existing_rows:
            raise RuntimeError(f"completed case would be overwritten: {case_id}")
        existing_rows[case_id] = rows[0]
        outcome = outcomes[case_id]
        state["cases"][case_id] = {
            "status": "complete",
            "final_state": outcome.get("final_state"),
            "error_type": outcome.get("error_type"),
            "attempt": attempt_number,
        }
        ordered_rows = [existing_rows[item] for item in part["case_ids"] if item in existing_rows]
        _atomic_csv(partition_dir / "answers.csv", ["id", "answer"], ordered_rows)
        event_path = partition_dir / "events.jsonl"
        prior = event_path.read_text(encoding="utf-8") if event_path.is_file() else ""
        appended = "".join(
            json.dumps({**event, "attempt": attempt_number}, ensure_ascii=False, sort_keys=True) + "\n"
            for event in events
        )
        _atomic_text(event_path, prior + appended)
        state["status"] = "complete" if len(state["cases"]) == 5 else "partial"
        state["calendar_time_ms"] = float(state.get("calendar_time_ms", 0.0)) + elapsed
        state.pop("active_case_id", None)
        _atomic_json(state_path, state)
        all_events = [
            json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        _write_partition_files(partition_dir, all_events, state)
        print(json.dumps({
            "partition_id": partition_id,
            "case_id": case_id,
            "completed": len(state["cases"]),
            "remaining": 5 - len(state["cases"]),
            "final_state": outcome.get("final_state"),
            "duration_ms": outcome.get("duration_ms"),
            "error_type": outcome.get("error_type"),
        }, ensure_ascii=False, sort_keys=True), flush=True)


def _ensure_run_metadata(run_root: Path, manifest: dict[str, Any]) -> None:
    path = run_root / "run.json"
    current = {
        "run_id": run_root.name,
        "commit": _git("rev-parse", "HEAD"),
        "dataset_sha256": manifest["dataset_sha256"],
        "corpus_sha256": _corpus_hash(),
        "models": {
            "llm": os.environ.get("LLM_MODEL"),
            "embedding": os.environ.get("EMB_MODEL"),
            "reranker": os.environ.get("RERANKER_MODEL"),
        },
        "parameters": {
            "temperature": os.environ.get("TEMPERATURE", "0.0"),
            "max_workers": os.environ.get("MAX_WORKERS"),
            "time_limit_seconds": os.environ.get("TIME_LIMIT_SECONDS"),
        },
    }
    if path.is_file() and _read_json(path, {}) != current:
        raise RuntimeError("run_id already belongs to different code, models, or parameters")
    if not path.is_file():
        _atomic_json(path, current)


def _write_partition_files(partition_dir: Path, events: list[dict[str, Any]], state: dict[str, Any]) -> None:
    _write_event_csv_at(partition_dir / "timings.csv", events, lambda event: isinstance(event.get("duration_ms"), (int, float)))
    _write_event_csv_at(partition_dir / "llm_metrics.csv", events, lambda event: event.get("kind") in {"llm", "api"})
    _write_stage_metrics_at(partition_dir / "stage_metrics.csv", events)
    errors = [event for event in events if event.get("error_count") or event.get("error_type")]
    _atomic_json(partition_dir / "errors.json", errors)
    _atomic_text(
        partition_dir / "report.md",
        f"# {state['partition_id']}\n\n- Status: {state['status']}\n- Completed: {len(state['cases'])}/5\n"
        f"- Calendar time: {state.get('calendar_time_ms', 0.0):.3f} ms\n",
    )


def _recalculate_partition_run(manifest: dict[str, Any], cases: list[dict[str, Any]], run_root: Path) -> None:
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for part in manifest["partitions"]:
        directory = run_root / "partitions" / part["partition_id"]
        rows.extend(_read_answer_rows(directory / "answers.csv").values())
        event_path = directory / "events.jsonl"
        if event_path.is_file():
            partition_events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            events.extend(partition_events)
            _write_architecture_metrics_at(
                directory,
                partition_events,
                _read_json(directory / "codex_evaluation.json", {}),
            )
    run_metadata = _read_json(run_root / "run.json", {})
    state = {
        "reason": None,
        "commit": run_metadata.get("commit") or _git("rev-parse", "HEAD"),
        "baseline_commit": BASELINE_COMMIT,
        "golden_sha256": manifest["dataset_sha256"],
        "corpus_sha256": _corpus_hash(),
        "case_count": 65,
        "run_dir": str(run_root),
    }
    state["codex_evaluations"] = _read_codex_evaluations(manifest, run_root)
    _write_outputs(cases, rows, events, state)


def _read_codex_evaluations(manifest: dict[str, Any], run_root: Path) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    partitions: list[dict[str, Any]] = []
    for part in manifest["partitions"]:
        path = run_root / "partitions" / part["partition_id"] / "codex_evaluation.json"
        payload = _read_json(path, {})
        if not payload:
            continue
        partitions.append(payload)
        for result in payload.get("cases", []):
            cases[str(result["case_id"])] = result
    return {"cases": cases, "partitions": partitions}


def _print_partition_status(manifest: dict[str, Any], run_root: Path) -> None:
    completed = 0
    result = []
    for part in manifest["partitions"]:
        state = _read_json(run_root / "partitions" / part["partition_id"] / "state.json", {})
        count = len([row for row in state.get("cases", {}).values() if row.get("status") == "complete"])
        completed += count
        result.append({"partition_id": part["partition_id"], "completed": count, "status": state.get("status", "pending")})
    print(json.dumps({"run_id": run_root.name, "completed": completed, "remaining": 65 - completed, "partitions": result}, ensure_ascii=False, indent=2))


def _read_answer_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {str(row["id"]): row for row in csv.DictReader(handle)}


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _atomic_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_event_csv_at(path: Path, events: list[dict[str, Any]], predicate) -> None:
    selected = [event for event in events if predicate(event)]
    keys = sorted({key for event in selected for key in event}) or ["kind", "stage", "question_id", "value"]
    _atomic_csv(path, keys, [{key: _cell(event.get(key, "")) for key in keys} for event in selected])


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
    has_url = any(os.environ.get(name) for name in ("LLM_BASE_URL", "BASE_URL", "OPENAI_BASE_URL"))
    local = _load_local_settings()
    has_key = any(os.environ.get(name) for name in ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_DEV")) or bool(local.get("LLM_DEV"))
    missing = [name for name, present in (("LLM_BASE_URL/BASE_URL/OPENAI_BASE_URL", has_url), ("LLM_API_KEY/OPENAI_API_KEY", has_key)) if not present]
    if missing:
        return "real run unavailable: process environment is missing " + ", ".join(missing)
    if not CORPUS.is_dir():
        return "real run unavailable: organizer corpus directory is missing"
    return None


def _configure_dev_local() -> None:
    local = _load_local_settings()
    if not local.get("LLM_DEV") and not os.environ.get("LLM_DEV"):
        raise RuntimeError("DEV_LOCAL unavailable: LLM_DEV is not configured")
    checks = (
        ("BGE-M3", "http://127.0.0.1:8081", "BAAI/bge-m3"),
        ("Qwen3", "http://127.0.0.1:8080", None),
    )
    models: dict[str, str] = {}
    for service, root, expected in checks:
        try:
            health = httpx.get(f"{root}/health", timeout=5.0)
            health.raise_for_status()
            payload = httpx.get(f"{root}/v1/models", timeout=5.0)
            payload.raise_for_status()
            ids = [str(row.get("id")) for row in payload.json().get("data", []) if row.get("id")]
        except Exception as exc:
            raise RuntimeError(f"DEV_LOCAL {service} unavailable: {type(exc).__name__}") from exc
        if not ids or (expected and expected not in ids):
            raise RuntimeError(f"DEV_LOCAL {service} exposes an incompatible model")
        models[service] = expected or ids[0]
    os.environ.update({
        "RAG_RUNTIME_MODE": "DEV_LOCAL",
        "LLM_BASE_URL": "https://matreshkaapi.mooo.com/v1",
        "LLM_MODEL": "deepseek-v4.1-flash",
        "EMBEDDER_BASE_URL": "http://127.0.0.1:8081/v1",
        "EMBEDDER_API_KEY": "local",
        "EMB_MODEL": models["BGE-M3"],
        "RERANKER_BASE_URL": "http://127.0.0.1:8080/v1",
        "RERANKER_API_KEY": "local",
        "RERANKER_MODEL": models["Qwen3"],
        "RERANKER_MAX_CONCURRENCY": "1",
        "RERANKER_DIAGNOSTIC_FULL": "1",
        "INDEX_DIR": str(ROOT / "submission" / "assets" / "bge_m3"),
        "API_MODE": "live",
    })


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
        fields = [*REQUIRED_FIELDS, "answer", "pipeline_state", "codex_score", "error"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            event = outcomes.get(str(case["id"]), {})
            writer.writerow({
                **{name: _cell(case[name]) for name in REQUIRED_FIELDS},
                "answer": answers.get(str(case["id"]), "N/A"),
                "pipeline_state": event.get("final_state", "N/A"),
                "codex_score": state.get("codex_evaluations", {}).get("cases", {}).get(str(case["id"]), {}).get("overall_score", "N/A"),
                "error": event.get("error_type") or state.get("reason") or "",
            })
    _write_event_csv("RAG_TIMINGS.csv", events, lambda e: isinstance(e.get("duration_ms"), (int, float)))
    _write_event_csv("RAG_LLM_METRICS.csv", events, lambda e: e.get("kind") in {"llm", "api"})
    _write_stage_metrics(events)
    completed = len(rows)
    evaluations = state.get("codex_evaluations", {}).get("partitions", [])
    evaluated = sum(int(item.get("case_count", 0)) for item in evaluations)
    scores = [float(case["overall_score"]) for item in evaluations for case in item.get("cases", [])]
    codex_score = f"{mean(scores):.4f}" if scores else "N/A"
    reason = state.get("reason") or "none"
    (REPORTS / "GOLDEN_EVALUATION_REPORT.md").write_text(
        "# Golden evaluation report\n\n"
        f"- Golden cases: 65\n- Full real pipeline completed: {completed}/65\n"
        f"- Codex evaluation completed: {evaluated}/65\n- Codex mean score: {codex_score}\n"
        f"- Status: {'in progress' if completed < 65 else 'complete'}\n"
        f"- Reason: {reason}\n- Commit: `{state['commit']}`\n"
        f"- Golden SHA-256: `{state['golden_sha256']}`\n- Corpus SHA-256: `{state['corpus_sha256']}`\n\n"
        "The Codex score is an internal diagnostic assessment, not the organizer score. "
        "All unavailable quality measures are N/A, not zero.\n",
        encoding="utf-8",
    )
    _write_failure_report(cases, answers, outcomes, state, completed, evaluated)
    _write_history_report()


def _write_failure_report(
    cases: list[dict[str, Any]], answers: dict[str, str], outcomes: dict[str, dict[str, Any]],
    state: dict[str, Any], completed: int, evaluated: int,
) -> None:
    by_id = {str(case["id"]): case for case in cases}
    evaluations = state.get("codex_evaluations", {}).get("cases", {})
    relevance_payload = _read_json(P03_RELEVANCE, {})
    evidence_ids = {
        str(case["question_id"]): [row["chunk_id"] for row in case.get("fragments", [])]
        for case in relevance_payload.get("cases", [])
    }
    lines = [
        "# Golden failure analysis", "",
        f"The current immutable run completed {completed}/65 cases and Codex evaluated {evaluated}/65. "
        "Unfinished cases remain N/A.", "",
    ]
    for case_id, evaluation in evaluations.items():
        case = by_id[case_id]
        failure = evaluation.get("failure_analysis", {})
        outcome = outcomes.get(case_id, {})
        lines.extend([
            f"## {case_id}", "",
            f"1. Expected behavior: {case['expected_behavior']}",
            f"2. Actual answer: {answers.get(case_id, 'N/A')}",
            f"3. Missing or incorrect assertions: {evaluation.get('reason', 'N/A')}",
            f"4. Independently available evidence: {', '.join(evidence_ids.get(case_id, [])) or 'N/A: no provision-level label required'}.",
            f"5. First information-loss stage: {failure.get('first_loss_stage', 'N/A')}",
            f"6. Confirmed cause or testable hypothesis: {failure.get('cause', 'N/A')}",
            f"7. General fix: {failure.get('fix', 'N/A')}",
            f"8. Retest result: {failure.get('retest', 'N/A')}",
            f"9. Side effects: {failure.get('side_effects', 'N/A')}",
            f"10. Terminal state: {outcome.get('final_state', 'N/A')}; initial error: {outcome.get('error_type') or 'none'}.",
            "",
        ])
    _atomic_text(REPORTS / "GOLDEN_FAILURE_ANALYSIS.md", "\n".join(lines))


def _write_history_report() -> None:
    manifest = _read_json(HISTORY_RUNS, {})
    rows = []
    for item in manifest.get("partitions", []):
        directory = PARTITION_RUNS / item["run_id"]
        evaluation = _read_json(directory / "partitions" / item["partition_id"] / "codex_evaluation.json", {})
        metadata = _read_json(directory / "run.json", {})
        if not evaluation:
            continue
        summary = evaluation.get("summary", {})
        rows.append({"partition": item["partition_id"], "run": item["run_id"], "commit": metadata.get("commit", item.get("commit")), "cases": evaluation.get("case_count", 0), "score": summary.get("mean_score"), "facts": summary.get("required_fact_recall", {}), "claims": summary.get("claim_support_rate", {}), "citations": summary.get("citation_coverage", {}), "technical": summary.get("technical_refusal_rate", {}), "false_refusal": summary.get("false_refusal_rate", {}), "calendar": summary.get("calendar_time_ms")})
    total_cases = sum(int(row["cases"]) for row in rows)
    score_sum = sum(float(row["score"]) * int(row["cases"]) for row in rows)
    def combined(name: str) -> tuple[int, int, float | None]:
        numerator = sum(int(row[name].get("numerator", 0)) for row in rows if isinstance(row[name], dict))
        denominator = sum(int(row[name].get("denominator", 0)) for row in rows if isinstance(row[name], dict))
        return numerator, denominator, numerator / denominator if denominator else None
    lines = [
        "# Baseline versus current", "",
        "This is mixed-version historical progress, not a quality estimate for one commit. Same-version comparison on identical IDs is N/A because no matched replay exists.", "",
        "| Partition | Run | Commit | Cases | Codex | Required facts | Claims supported | Citations | Technical refusals | Calendar s |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    def ratio(value: Any) -> str:
        if not isinstance(value, dict):
            return "N/A"
        return f'{value.get("numerator")}/{value.get("denominator")} ({float(value.get("value", 0)):.1%})'
    for row in rows:
        lines.append(f'| {row["partition"]} | {row["run"]} | `{row["commit"]}` | {row["cases"]} | {float(row["score"]):.4f} | {ratio(row["facts"])} | {ratio(row["claims"])} | {ratio(row["citations"])} | {ratio(row["technical"])} | {float(row["calendar"] or 0) / 1000:.3f} |')
    lines.extend(["", f"Mixed-version total: {total_cases}/65; Codex case macro: {score_sum / total_cases:.4f}." if total_cases else "Mixed-version total: 0/65."])
    for name, title in (("facts", "Required Fact Recall"), ("claims", "Claim Support Rate"), ("citations", "Citation Coverage"), ("technical", "Technical Refusal Rate"), ("false_refusal", "False Refusal Rate")):
        numerator, denominator, value = combined(name)
        lines.append(f"- {title}: {numerator}/{denominator} ({value:.2%})" if value is not None else f"- {title}: N/A")
    lines.extend(["", "External organizer checkpoint: 10.083 points in 700.8 seconds. It is not converted to the Codex scale.", f"Baseline code commit: `{BASELINE_COMMIT}`.", ""])
    _atomic_text(REPORTS / "RAG_BASELINE_VS_CURRENT.md", "\n".join(lines))


def _write_event_csv(name: str, events: list[dict[str, Any]], predicate) -> None:
    selected = [event for event in events if predicate(event)]
    keys = sorted({key for event in selected for key in event}) or ["kind", "stage", "question_id", "value"]
    with (REPORTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for event in selected:
            writer.writerow({key: _cell(event.get(key, "")) for key in keys})


def _write_stage_metrics(events: list[dict[str, Any]]) -> None:
    _write_stage_metrics_at(REPORTS / "RAG_STAGE_METRICS.csv", events)


def _write_stage_metrics_at(path: Path, events: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for event in events:
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)) and event.get("kind") != "api":
            stage = "question_total" if event.get("kind") == "outcome" and event.get("stage") == "question" else str(event.get("stage"))
            grouped[stage].append(float(duration))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["stage", "definition", "count", "total_ms", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "errors", "retries"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name in STAGE_NAMES:
            values = grouped.get(name, [])
            relevant = [
                event for event in events
                if event.get("kind") != "api" and (
                    event.get("stage") == name
                    or (name == "question_total" and event.get("kind") == "outcome" and event.get("stage") == "question")
                )
            ]
            writer.writerow({
                "stage": name, "definition": "measured pipeline stage duration",
                "count": len(values) if values else "N/A", "total_ms": sum(values) if values else "N/A",
                "mean_ms": mean(values) if values else "N/A", "p50_ms": _percentile(values, .5),
                "p95_ms": _percentile(values, .95), "p99_ms": _percentile(values, .99),
                "max_ms": max(values) if values else "N/A",
                "errors": sum(int(event.get("error_count", 0) or bool(event.get("error_type"))) for event in relevant),
                "retries": sum(int(event.get("retry_count", event.get("retries", 0)) or 0) for event in relevant),
            })


def _write_architecture_metrics_at(
    directory: Path, events: list[dict[str, Any]], evaluation: dict[str, Any]
) -> None:
    fields = [
        "level", "element", "metric", "k", "numerator", "denominator", "value",
        "averaging", "label_source", "observations", "na_reason",
    ]
    rows: list[dict[str, Any]] = []

    def add(element: str, metric: str, *, value: Any = "N/A", numerator: Any = "N/A",
            denominator: Any = "N/A", k: Any = "", averaging: str = "micro",
            source: str = "runtime events", observations: Any = "N/A", reason: str = "") -> None:
        rows.append({
            "level": "partition", "element": element, "metric": metric, "k": k,
            "numerator": numerator, "denominator": denominator, "value": value,
            "averaging": averaging, "label_source": source, "observations": observations,
            "na_reason": reason,
        })

    relevance = _load_relevance_labels({str(event.get("question_id")) for event in events})
    quality_labels = "N/A: independent entity, intent, fact, and claim-error labels are not present for this partition"
    for metric in ("Intent Accuracy", "Entity Precision", "Entity Recall", "Entity F1", "Date Accuracy", "Version Accuracy", "Required Facts Extraction Precision", "Required Facts Extraction Recall", "Required Facts Extraction F1"):
        add("DeepSeek #1 analysis", metric, reason=quality_labels)
    analysis_events = [e for e in events if e.get("kind") == "quality" and e.get("stage") == "analysis"]
    add("query preparation", "query count", value=sum(int(e.get("lexical_queries_count", 0)) + int(e.get("semantic_queries_count", 0)) for e in analysis_events), observations=len(analysis_events))
    add("query preparation", "Query Coverage", reason="N/A: no independent fact-to-query mapping")
    if relevance:
        for row in _retrieval_metric_rows(events, relevance):
            add(**row)
    else:
        for element in ("BM25", "FAISS", "RRF", "Qwen3 reranker"):
            for k in (5, 8, 10, 24):
                for metric in ("Document Recall", "Section Recall", "Fragment Recall", "Precision", "MRR", "nDCG", "Hit Rate", "Required Evidence Coverage"):
                    add(element, metric, k=k, averaging="macro over annotated queries", reason="N/A: no independent provision-level relevance annotation for this partition")
    embedding_events = [e for e in events if e.get("kind") == "rag" and e.get("stage") == "embeddings"]
    if embedding_events:
        add("BGE-M3", "embedding dimension", value=embedding_events[0].get("embedding_dimension"), observations=len(embedding_events))
        add("BGE-M3", "mean vector norm", value=mean(float(e["vector_norm"]) for e in embedding_events), observations=len(embedding_events))
    rerank_events = [e for e in events if e.get("kind") == "rag" and e.get("stage") == "rerank"]
    if rerank_events:
        add("Qwen3 reranker", "mean candidates", value=mean(float(e.get("candidates_count", 0)) for e in rerank_events), observations=len(rerank_events))
        add("Qwen3 reranker", "fallback rate", numerator=sum(bool(e.get("fallback_used")) for e in rerank_events), denominator=len(rerank_events), value=sum(bool(e.get("fallback_used")) for e in rerank_events) / len(rerank_events), observations=len(rerank_events))
    for element, names in {
        "evidence selection": ("Evidence Precision", "Evidence Recall", "Required Fact Coverage", "Evidence Selection Loss"),
        "DeepSeek #2 generation": ("Required Fact Recall", "Claim Precision", "Claim Support Rate", "Unsupported Claim Rate", "Hallucination Rate"),
        "DeepSeek #3 verification": ("Error Precision", "Error Recall", "Error F1", "False Acceptance", "False Rejection", "False Pass", "Coverage"),
        "revision": ("Recovery Success Rate", "New Defect Rate", "Repeated Error Rate", "Completeness Delta"),
    }.items():
        for name in names:
            if relevance and element == "evidence selection":
                continue
            if relevance and element == "DeepSeek #2 generation" and name in {
                "Required Fact Recall", "Claim Precision", "Claim Support Rate", "Unsupported Claim Rate"
            }:
                continue
            if relevance and element == "revision" and name == "Recovery Success Rate":
                continue
            add(element, name, reason=quality_labels)
    if relevance:
        reviewed = evaluation.get("summary", {}).get("claim_support_rate", {})
        facts = evaluation.get("summary", {}).get("required_fact_recall", {})
        if isinstance(reviewed, dict):
            add("DeepSeek #2 generation", "Claim Support Rate", numerator=reviewed.get("numerator"), denominator=reviewed.get("denominator"), value=reviewed.get("value"), source="Codex independent claim review", observations=evaluation.get("case_count"))
            add("DeepSeek #2 generation", "Claim Precision", numerator=reviewed.get("numerator"), denominator=reviewed.get("denominator"), value=reviewed.get("value"), source="Codex independent claim review", observations=evaluation.get("case_count"))
            denominator = int(reviewed.get("denominator", 0) or 0)
            unsupported = denominator - int(reviewed.get("numerator", 0) or 0)
            add("DeepSeek #2 generation", "Unsupported Claim Rate", numerator=unsupported, denominator=denominator, value=unsupported / denominator if denominator else None, source="Codex independent claim review", observations=evaluation.get("case_count"))
        if isinstance(facts, dict):
            add("DeepSeek #2 generation", "Required Fact Recall", numerator=facts.get("numerator"), denominator=facts.get("denominator"), value=facts.get("value"), source="Codex independent final-answer review", observations=evaluation.get("case_count"))
        revision_attempts = len([event for event in events if event.get("kind") == "llm" and event.get("stage") == "generation_revision"])
        successful_revisions = len({
            str(event.get("question_id")) for event in events
            if event.get("kind") == "quality" and event.get("stage") == "reverification" and event.get("passed") == 1
        })
        if revision_attempts:
            add("revision", "Recovery Success Rate", numerator=successful_revisions, denominator=revision_attempts, value=successful_revisions / revision_attempts, source="runtime revision and reverification events", observations=revision_attempts)
    summary = evaluation.get("summary", {})
    for source_name, metric_name in (
        ("required_fact_recall", "Required Fact Recall"),
        ("claim_support_rate", "Claim Support Rate"),
        ("citation_coverage", "Citation Coverage"),
        ("technical_refusal_rate", "Technical Refusal Rate"),
        ("false_refusal_rate", "False Refusal Rate"),
        ("full_pass_rate", "Full Pass Rate"),
    ):
        value = summary.get(source_name)
        if isinstance(value, dict):
            add("final answer", metric_name, numerator=value.get("numerator"), denominator=value.get("denominator"), value=value.get("value"), source="Codex manual review", observations=evaluation.get("case_count"))
        else:
            add("final answer", metric_name, reason="N/A: Codex evaluation not completed")
    add("final answer", "Codex score", value=summary.get("mean_score", "N/A"), averaging="case macro", source="Codex six-criterion review", observations=evaluation.get("case_count", "N/A"), reason="" if summary.get("mean_score") is not None else "N/A: Codex evaluation not completed")
    _atomic_csv(directory / "architecture_metrics.csv", fields, rows)

    case_fields = ["question_id", "stage", "kind", "metrics_json", "codex_score"]
    scores = {str(row["case_id"]): row.get("overall_score") for row in evaluation.get("cases", [])}
    case_rows = [
        {
            "question_id": event.get("question_id"), "stage": event.get("stage"),
            "kind": event.get("kind"), "metrics_json": _cell(event),
            "codex_score": scores.get(str(event.get("question_id")), "N/A"),
        }
        for event in events if event.get("question_id") is not None
    ]
    _atomic_csv(directory / "case_metrics.csv", case_fields, case_rows)
    if relevance:
        _write_fact_flow_at(directory / "fact_flow.csv", events, relevance, evaluation)


def _load_relevance_labels(question_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not P03_RELEVANCE.is_file() or not INDEX_CHUNKS.is_file():
        return {}
    payload = json.loads(P03_RELEVANCE.read_text(encoding="utf-8"))
    annotated = {str(row["question_id"]): row for row in payload.get("cases", [])}
    selected = question_ids & set(annotated)
    if not selected:
        return {}
    chunks_payload = json.loads(INDEX_CHUNKS.read_text(encoding="utf-8"))
    chunks = chunks_payload.get("chunks", []) if isinstance(chunks_payload, dict) else chunks_payload
    by_id = {str(row["chunk_id"]): row for row in chunks}
    trace_catalog: dict[str, dict[str, str]] = {}
    for chunk in chunks:
        trace_payload = "\u241f".join(
            str(chunk.get(name) or "") for name in ("document", "section", "text")
        )
        trace = hashlib.sha256(trace_payload.encode("utf-8")).hexdigest()[:16]
        document_id = hashlib.sha256(chunk["document"].encode("utf-8")).hexdigest()[:16]
        trace_catalog[trace] = {
            "document_id": document_id,
            "section_id": f'{document_id}#{str(chunk.get("section") or "")}',
        }
    result: dict[str, dict[str, Any]] = {}
    for question_id in selected:
        labels = annotated[question_id].get("fragments", [])
        missing = [row["chunk_id"] for row in labels if row["chunk_id"] not in by_id]
        if missing:
            raise ValueError(f"unresolved relevance chunks for {question_id}: {missing}")
        fragment_rows = []
        for label in labels:
            chunk = by_id[label["chunk_id"]]
            trace_payload = "\u241f".join(
                str(chunk.get(name) or "") for name in ("document", "section", "text")
            )
            fragment_rows.append({
                **label,
                "trace_id": hashlib.sha256(trace_payload.encode("utf-8")).hexdigest()[:16],
                "document": chunk["document"],
                "document_id": hashlib.sha256(chunk["document"].encode("utf-8")).hexdigest()[:16],
                "section": str(chunk.get("section") or ""),
            })
        result[question_id] = {
            "fragments": fragment_rows,
            "trace_catalog": trace_catalog,
            "non_retrieval_fact_ids": annotated[question_id].get("non_retrieval_fact_ids", []),
        }
    return result


def _retrieval_metric_rows(
    events: list[dict[str, Any]], relevance: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    first_events: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        question_id = str(event.get("question_id"))
        stage = str(event.get("stage"))
        if event.get("kind") == "rag" and question_id in relevance:
            first_events.setdefault((question_id, stage), event)
    rows: list[dict[str, Any]] = []
    stage_names = {
        "BM25": ("bm25", False),
        "FAISS": ("faiss", False),
        "RRF": ("fusion", False),
        "Qwen3 reranker": ("rerank", False),
        "Qwen3 diagnostic full order": ("rerank", True),
    }
    for element, (stage, diagnostic) in stage_names.items():
        cutoffs = (5, 8, 10, 24) if element != "Qwen3 reranker" else (5, 8)
        for cutoff in cutoffs:
            per_dimension: dict[str, list[dict[str, float | int]]] = defaultdict(list)
            required_retrieved = required_total = 0
            for question_id, annotation in relevance.items():
                event = first_events.get((question_id, stage))
                if event is None:
                    continue
                key = "diagnostic_ranked_fragment_ids" if diagnostic else "ranked_fragment_ids"
                trace_ranking = list(event.get(key, []))
                if diagnostic and not event.get("diagnostic_full_order"):
                    continue
                fragment_lookup = {row["trace_id"]: row for row in annotation["fragments"]}
                trace_catalog = annotation["trace_catalog"]
                trace_to_grade = {trace: int(row["relevance"]) for trace, row in fragment_lookup.items()}
                document_relevance: dict[str, int] = {}
                section_relevance: dict[str, int] = {}
                for row in annotation["fragments"]:
                    document_relevance[row["document_id"]] = max(
                        document_relevance.get(row["document_id"], 0), int(row["relevance"])
                    )
                    section_id = f'{row["document_id"]}#{row["section"]}'
                    section_relevance[section_id] = max(
                        section_relevance.get(section_id, 0), int(row["relevance"])
                    )
                doc_ranking = list(event.get("ranked_document_ids", []))
                section_ranking = []
                for trace in trace_ranking:
                    value = trace_catalog.get(trace, {}).get("section_id")
                    if value and value not in section_ranking:
                        section_ranking.append(value)
                per_dimension["Document"].append(labeled_ranking_metrics(doc_ranking, document_relevance, cutoff))
                per_dimension["Section"].append(labeled_ranking_metrics(section_ranking, section_relevance, cutoff))
                per_dimension["Fragment"].append(labeled_ranking_metrics(trace_ranking, trace_to_grade, cutoff))
                required = {row["trace_id"] for row in annotation["fragments"] if row.get("necessity") == "required"}
                required_retrieved += len(required & set(trace_ranking[:cutoff]))
                required_total += len(required)
            for dimension, metrics in per_dimension.items():
                if not metrics:
                    continue
                for metric_key, label in (("recall", "Recall"), ("precision", "Precision"), ("mrr", "MRR"), ("ndcg", "nDCG"), ("hit_rate", "Hit Rate")):
                    rows.append({
                        "element": element,
                        "metric": f"{dimension} {label}",
                        "k": cutoff,
                        "value": mean(float(item[metric_key]) for item in metrics),
                        "numerator": sum(float(item[metric_key]) for item in metrics),
                        "denominator": len(metrics),
                        "averaging": "case macro",
                        "source": "P03 independent provision-level relevance annotation v1",
                        "observations": len(metrics),
                    })
            if required_total:
                rows.append({
                    "element": element, "metric": "Required Evidence Coverage", "k": cutoff,
                    "value": required_retrieved / required_total,
                    "numerator": required_retrieved, "denominator": required_total,
                    "averaging": "micro", "source": "P03 independent provision-level relevance annotation v1",
                    "observations": len(relevance),
                })
    rows.extend(_selection_loss_rows(relevance, first_events))
    return rows


def _selection_loss_rows(
    relevance: dict[str, dict[str, Any]],
    first_events: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    present = lost = relevant_selected = relevant_total = selected_total = 0
    required_selected = required_total = 0
    for question_id, annotation in relevance.items():
        rerank = first_events.get((question_id, "rerank"), {})
        ranked = set(rerank.get("ranked_fragment_ids", []))
        required = {row["trace_id"] for row in annotation["fragments"] if row.get("necessity") == "required"}
        chosen = set(rerank.get("ranked_fragment_ids", []))
        relevant = {row["trace_id"] for row in annotation["fragments"]}
        available = required & ranked
        present += len(available)
        lost += len(available - chosen)
        relevant_selected += len(relevant & chosen)
        relevant_total += len(relevant)
        selected_total += len(chosen)
        required_selected += len(required & chosen)
        required_total += len(required)
    source = "P03 independent provision-level relevance annotation v1"
    return [
        {"element": "evidence selection", "metric": "Evidence Precision", "value": relevant_selected / selected_total if selected_total else None, "numerator": relevant_selected, "denominator": selected_total, "averaging": "micro", "source": source, "observations": len(relevance)},
        {"element": "evidence selection", "metric": "Evidence Recall", "value": relevant_selected / relevant_total if relevant_total else None, "numerator": relevant_selected, "denominator": relevant_total, "averaging": "micro", "source": source, "observations": len(relevance)},
        {"element": "evidence selection", "metric": "Required Fact Coverage", "value": required_selected / required_total if required_total else None, "numerator": required_selected, "denominator": required_total, "averaging": "micro by required evidence", "source": source, "observations": len(relevance)},
        {"element": "evidence selection", "metric": "Evidence Selection Loss", "value": lost / present if present else 0.0, "numerator": lost, "denominator": present, "averaging": "micro", "source": source, "observations": len(relevance)},
    ]


def _write_fact_flow_at(
    path: Path, events: list[dict[str, Any]], relevance: dict[str, dict[str, Any]],
    evaluation: dict[str, Any],
) -> None:
    first_events: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        question_id = str(event.get("question_id"))
        stage = str(event.get("stage"))
        if event.get("kind") == "rag":
            first_events.setdefault((question_id, stage), event)
    published = {
        str(row["case_id"]): set(row.get("published_fact_ids", []))
        for row in evaluation.get("cases", [])
    }
    rows = []
    for question_id, annotation in relevance.items():
        fact_sources: dict[str, set[str]] = defaultdict(set)
        for fragment in annotation["fragments"]:
            for fact_id in fragment.get("required_fact_ids", []):
                fact_sources[str(fact_id)].add(fragment["trace_id"])
        all_fact_ids = set(fact_sources) | set(annotation.get("non_retrieval_fact_ids", []))
        bm25 = set(first_events.get((question_id, "bm25"), {}).get("ranked_fragment_ids", []))
        faiss = set(first_events.get((question_id, "faiss"), {}).get("ranked_fragment_ids", []))
        fusion = set(first_events.get((question_id, "fusion"), {}).get("ranked_fragment_ids", []))
        rerank = set(first_events.get((question_id, "rerank"), {}).get("ranked_fragment_ids", []))
        chosen_evidence = rerank
        for fact_id in sorted(all_fact_ids):
            sources = fact_sources.get(fact_id, set())
            retrieval_applicable = bool(sources)
            stages: list[tuple[str, bool | None]] = [
                ("analysis_recognized", None),
                ("source_found_bm25_or_faiss", bool(sources & (bm25 | faiss)) if retrieval_applicable else None),
                ("preserved_after_rrf", bool(sources & fusion) if retrieval_applicable else None),
                ("preserved_after_qwen3", bool(sources & rerank) if retrieval_applicable else None),
                ("selected_as_evidence", bool(sources & chosen_evidence) if retrieval_applicable else None),
                ("used_by_generator", None),
                ("verified", None),
                ("preserved_after_revision", None),
                ("published", fact_id in published.get(question_id, set())),
            ]
            first_loss = next((stage for stage, value in stages[1:] if value is False), "none")
            for stage, value in stages:
                reason = ""
                if value is None:
                    reason = "N/A: independent fact IDs cannot be aligned to this model-internal stage" if retrieval_applicable else "N/A: behavioral fact does not require corpus retrieval"
                rows.append({
                    "question_id": question_id, "fact_id": fact_id, "stage": stage,
                    "present": "N/A" if value is None else int(value),
                    "first_loss_stage": first_loss, "supporting_fragment_ids": sorted(sources),
                    "reason": reason,
                })
    _atomic_csv(
        path,
        ["question_id", "fact_id", "stage", "present", "first_loss_stage", "supporting_fragment_ids", "reason"],
        [{**row, "supporting_fragment_ids": _cell(row["supporting_fragment_ids"])} for row in rows],
    )


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
