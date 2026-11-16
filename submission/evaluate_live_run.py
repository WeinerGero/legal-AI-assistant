"""Reproducible live-model probes and report assembly for local RAG evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from submission.model_client import ModelClient, _load_local_settings
from submission.pipeline import _validate_analysis, _validate_generation, _validate_verification

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verification-probe")
    verify.add_argument("--output", type=Path, required=True)
    examples = subparsers.add_parser("stage-examples")
    examples.add_argument("--output", type=Path, required=True)
    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verification-probe":
        run_verification_probe(args.output)
    elif args.command == "stage-examples":
        run_stage_examples(args.output)
    else:
        summarize_run(args.run_dir)
    return 0


def run_verification_probe(output: Path) -> None:
    os.environ.setdefault("LLM_BASE_URL", "https://matreshkaapi.mooo.com/v1")
    os.environ.setdefault("LLM_MODEL", "deepseek-v4.1-flash")
    os.environ.setdefault("API_MODE", "live")
    client = ModelClient(timeout=60.0, max_tokens=1_536, temperature=0.0)
    local = _load_local_settings()
    if os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"):
        key_source = "exported platform-compatible key"
    elif local.get("LLM_DEV") and client.api_key == local["LLM_DEV"]:
        key_source = "LLM_DEV"
    else:
        raise RuntimeError("the live client did not select LLM_DEV or an exported platform key")
    prompt = (ROOT / "core" / "prompts" / "verification_v2.txt").read_text(encoding="utf-8")
    cases = json.loads(
        (ROOT / "submission" / "prompt_experiment_cases.json").read_text(encoding="utf-8")
    )["cases"]
    results = []
    for case in cases:
        request = {
            "messages": [{"role": "user", "content": case["input"]["standalone_question"]}],
            **case["input"],
        }
        started = perf_counter()
        raw = client.complete_json(
            prompt, json.dumps(request, ensure_ascii=False), stage="verification_controlled"
        )
        parsed = _parse_json(raw)
        parser_error = None
        try:
            _validate_verification(
                parsed, case["input"]["generation"], case["input"]["evidence"]
            )
        except Exception as exc:
            parser_error = f"{type(exc).__name__}: {exc}"
        results.append(
            {
                "id": case["id"],
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "expected": case["expected_output"],
                "actual": parsed,
                "parser_error": parser_error,
            }
        )
    payload = {
        "provenance": "real DeepSeek API responses on independently labeled controlled cases",
        "model": client.llm_model,
        "base_url": client.base_url,
        "key_source": key_source,
        "results": results,
        "metrics": _verification_metrics(results),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_stage_examples(output: Path) -> None:
    os.environ.setdefault("LLM_BASE_URL", "https://matreshkaapi.mooo.com/v1")
    os.environ.setdefault("LLM_MODEL", "deepseek-v4.1-flash")
    os.environ.setdefault("API_MODE", "live")
    client = ModelClient(timeout=60.0, max_tokens=1_536, temperature=0.0)
    cases = json.loads(
        (ROOT / "submission" / "prompt_experiment_cases.json").read_text(encoding="utf-8")
    )["cases"]
    supported = next(row for row in cases if row["id"] == "supported_answer")["input"]
    examples: dict[str, Any] = {}
    analysis_prompt = (ROOT / "core" / "prompts" / "analysis_v2.txt").read_text(encoding="utf-8")
    analysis_request = {
        "messages": [{"role": "user", "content": supported["standalone_question"]}]
    }
    started = perf_counter()
    analysis = _parse_json(
        client.complete_json(
            analysis_prompt,
            json.dumps(analysis_request, ensure_ascii=False),
            stage="analysis_example",
        )
    )
    _validate_analysis(analysis)
    examples["analysis"] = {
        "input": analysis_request,
        "output": analysis,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }
    generation_prompt = (ROOT / "core" / "prompts" / "generation_v2.txt").read_text(encoding="utf-8")
    generation_request = {
        "mode": "evaluation_example",
        "target_language": "ru",
        "question": supported["standalone_question"],
        "requested_facts": supported["requested_facts"],
        "evidence": supported["evidence"],
    }
    started = perf_counter()
    generation = _parse_json(
        client.complete_json(
            generation_prompt,
            json.dumps(generation_request, ensure_ascii=False),
            stage="generation_example",
        )
    )
    _validate_generation(generation, supported["evidence"])
    examples["generation"] = {
        "input": generation_request,
        "output": generation,
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "provenance": "real DeepSeek API responses",
                "model": client.llm_model,
                "base_url": client.base_url,
                "examples": examples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _verification_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    true_positive = false_positive = false_negative = true_negative = 0
    status_correct = status_total = 0
    verdict_correct = 0
    for row in results:
        expected = row["expected"]
        actual = row["actual"]
        verdict_correct += actual.get("verdict") == expected["verdict"]
        expected_by_id = {item["claim_id"]: item for item in expected["claim_checks"]}
        actual_by_id = {item.get("claim_id"): item for item in actual.get("claim_checks", [])}
        for claim_id, expected_check in expected_by_id.items():
            expected_error = expected_check["status"] != "supported"
            actual_error = actual_by_id.get(claim_id, {}).get("status") != "supported"
            if expected_error and actual_error:
                true_positive += 1
            elif expected_error:
                false_negative += 1
            elif actual_error:
                false_positive += 1
            else:
                true_negative += 1
            status_correct += (
                actual_by_id.get(claim_id, {}).get("status") == expected_check["status"]
            )
            status_total += 1
    precision = _divide(true_positive, true_positive + false_positive)
    recall = _divide(true_positive, true_positive + false_negative)
    f1 = _divide(2 * precision * recall, precision + recall) if precision is not None and recall is not None else None
    durations = sorted(row["duration_ms"] for row in results)
    return {
        "error_detection_precision": _metric(true_positive, true_positive + false_positive),
        "error_detection_recall": _metric(true_positive, true_positive + false_negative),
        "error_detection_f1": {"value": f1, "numerator": None, "denominator": None},
        "false_positive_rate": _metric(false_positive, false_positive + true_negative),
        "false_negative_rate": _metric(false_negative, false_negative + true_positive),
        "claim_status_accuracy": _metric(status_correct, status_total),
        "verdict_accuracy": _metric(verdict_correct, len(results)),
        "parser_compatibility": _metric(
            sum(row["parser_error"] is None for row in results), len(results)
        ),
        "duration_ms": {
            "mean": mean(durations),
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "max": max(durations),
        },
    }


def summarize_run(run_dir: Path) -> None:
    metrics = json.loads((run_dir / "metrics.summary.json").read_text(encoding="utf-8"))
    quality = json.loads((run_dir / "quality.json").read_text(encoding="utf-8"))
    controlled_path = run_dir / "verification_controlled.json"
    controlled = (
        json.loads(controlled_path.read_text(encoding="utf-8"))
        if controlled_path.is_file()
        else None
    )
    events = [
        json.loads(line)
        for line in (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = {
        "scope": {
            "complete_rag": False,
            "reason": "BGE-M3 query endpoint returned HTTP 404; FAISS and true fusion metrics are N/A",
            "raw_api_payloads_committed": False,
        },
        "models": metrics["config"]["models"],
        "run": metrics["run"],
        "stages": metrics["stages"],
        "stage_performance": _stage_performance(events),
        "offline_original_query_retrieval_control": quality["summary"]["retrieval"],
        "independent_final_answer_checks": quality["summary"]["solution_predictions"],
        "controlled_verification": controlled["metrics"] if controlled else None,
        "unavailable": {
            "analysis_accuracy": "N/A: independent entity/date/intent labels are not present for all 18 questions",
            "faiss": "N/A: real BGE-M3 query embeddings unavailable (HTTP 404)",
            "fusion_gain": "N/A: no FAISS query ranking",
            "qwen_gain": "N/A: live run metrics retained document IDs, not exact section rankings",
            "generation_claim_precision_recall": "N/A: structured raw Generation responses were not retained in live mode",
            "three_run_stability": "N/A: required BGE-M3 service unavailable; only one partial run executed",
        },
    }
    (run_dir / "live_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "live_report.md").write_text(_render_report(report), encoding="utf-8")


def _render_report(report: dict[str, Any]) -> str:
    run = report["run"]
    final = report["independent_final_answer_checks"]["final_answer_quality"]
    controlled = report["controlled_verification"]
    lines = [
        "# Live partial RAG evaluation",
        "",
        "This is not a complete RAG run: BGE-M3 query embeddings returned HTTP 404.",
        "",
        "## Run",
        "",
        f"- Duration: {run['duration_ms'] / 1000:.3f} s",
        f"- Successful pipeline questions: {run['questions_successful']}/{run['questions_total']}",
        f"- API calls/errors/retries: {run['api_calls']}/{run['api_errors']}/{run['api_retries']}",
        f"- Tokens prompt/completion/total: {run['tokens']['prompt_tokens']}/{run['tokens']['completion_tokens']}/{run['tokens']['total_tokens']}",
        f"- Technical refusals: {run['technical_refusals']}/{run['questions_total']}",
        "",
        "## Stage performance",
        "",
        "| Stage | Count | Mean ms | p50 | p90 | p95 | Max |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for stage, values in report["stage_performance"].items():
        lines.append(
            f"| {stage} | {values['count']} | {values['mean_ms']:.3f} | "
            f"{values['p50_ms']:.3f} | {values['p90_ms']:.3f} | "
            f"{values['p95_ms']:.3f} | {values['max_ms']:.3f} |"
        )
    lines.extend(
        [
        "",
        "## Independent final-answer checks",
        "",
        f"- Required facts: {final['required_fact_recall']['numerator']}/{final['required_fact_recall']['denominator']} ({final['required_fact_recall']['value']:.6f})",
        f"- Document citations: {final['citation_document_coverage']['numerator']}/{final['citation_document_coverage']['denominator']} ({final['citation_document_coverage']['value']:.6f})",
        f"- Section citations: {final['citation_section_coverage']['numerator']}/{final['citation_section_coverage']['denominator']} ({final['citation_section_coverage']['value']:.6f})",
        f"- Technical refusals: {final['technical_refusal_rate']['numerator']}/{final['technical_refusal_rate']['denominator']} ({final['technical_refusal_rate']['value']:.6f})",
        ]
    )
    if controlled:
        lines.extend(
            [
                "",
                "## Controlled real Verification probe",
                "",
                f"- Error precision: {_format_metric(controlled['error_detection_precision'])}",
                f"- Error recall: {_format_metric(controlled['error_detection_recall'])}",
                f"- Error F1: {controlled['error_detection_f1']['value']}",
                f"- Claim status accuracy: {_format_metric(controlled['claim_status_accuracy'])}",
                f"- Verdict accuracy: {_format_metric(controlled['verdict_accuracy'])}",
                f"- Parser compatibility: {_format_metric(controlled['parser_compatibility'])}",
            ]
        )
    lines.extend(["", "## N/A", ""])
    lines.extend(f"- {key}: {value}" for key, value in report["unavailable"].items())
    return "\n".join(lines) + "\n"


def _parse_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    return json.loads(text)


def _stage_performance(events: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[float]] = {}
    for event in events:
        duration = event.get("duration_ms")
        if not isinstance(duration, (int, float)):
            continue
        key = f"{event.get('kind', 'unknown')}.{event.get('stage', 'unknown')}"
        grouped.setdefault(key, []).append(float(duration))
    return {
        key: {
            "count": len(values),
            "mean_ms": mean(values),
            "p50_ms": _percentile(sorted(values), 0.50),
            "p90_ms": _percentile(sorted(values), 0.90),
            "p95_ms": _percentile(sorted(values), 0.95),
            "max_ms": max(values),
        }
        for key, values in sorted(grouped.items())
    }


def _divide(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"numerator": numerator, "denominator": denominator, "value": _divide(numerator, denominator)}


def _percentile(values: list[float], fraction: float) -> float:
    index = max(0, min(len(values) - 1, round((len(values) - 1) * fraction)))
    return values[index]


def _format_metric(metric: dict[str, Any]) -> str:
    return f"{metric['numerator']}/{metric['denominator']} ({metric['value']})"


if __name__ == "__main__":
    raise SystemExit(main())
