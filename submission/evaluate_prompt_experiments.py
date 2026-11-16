"""Offline comparison of baseline and candidate prompts without changing runtime defaults."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from submission.pipeline import _validate_verification

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "core" / "prompts"
BASELINE = {
    "analysis": PROMPTS / "analysis_v2.txt",
    "generation": PROMPTS / "generation_v2.txt",
    "verification": PROMPTS / "verification_v2.txt",
}
CANDIDATE = {
    "analysis": PROMPTS / "experiments" / "analysis_v3_candidate.txt",
    "generation": PROMPTS / "experiments" / "generation_v3_candidate.txt",
    "verification": PROMPTS / "experiments" / "verification_v3_candidate.txt",
    "reranker_instruction": PROMPTS / "experiments" / "qwen3_reranker_candidate.txt",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "submission" / "prompt_experiment_cases.json"
    )
    parser.add_argument(
        "--rag-report", type=Path, default=ROOT / "submission" / "evaluation_report.json"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "submission" / "prompt_experiment_report.json"
    )
    args = parser.parse_args(argv)
    report = evaluate(args.cases, args.rag_report)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return 0


def evaluate(cases_path: Path, rag_report_path: Path) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    rag_report = json.loads(rag_report_path.read_text(encoding="utf-8"))
    simulated = [_validate_case(case) for case in cases]
    retrieval = rag_report["summary"]["retrieval"]
    live_proxy_available = bool(
        os.getenv("LLM_BASE_URL")
        and (os.getenv("LLM_API_KEY") or os.getenv("LLM_DEV"))
    )
    return {
        "scope": {
            "baseline_is_runtime_default": True,
            "candidate_connected_to_runtime": False,
            "real_api_executed": False,
            "real_api_reason": (
                "Not executed: no proxy URL/key were exported to the process; .env was not read."
                if not live_proxy_available
                else "Not executed automatically: a controlled record/replay corpus is required for a fair comparison."
            ),
        },
        "prompt_hashes": {
            "baseline": {name: _sha256(path) for name, path in BASELINE.items()},
            "candidate": {name: _sha256(path) for name, path in CANDIDATE.items()},
        },
        "prompt_design_comparison": {
            "analysis": [
                "separate role and evidence boundary",
                "atomic requested_facts",
                "explicit event-date and version preservation",
                "strict existing JSON field set",
            ],
            "generation": [
                "one independently verifiable assertion per claim",
                "answer-to-claims coverage rule",
                "version/date/identifier checks",
                "existing answer_type and claims schema retained",
            ],
            "verification": [
                "content, revision, date, identifier and completeness checks separated",
                "citation existence is not sufficient",
                "evidence_ids must remain identical",
                "every claim checked exactly once with a nonempty reason",
                "existing verdict and status values retained",
            ],
        },
        "api_capability_status": {
            "current_proxy_contract": {
                "chat_completions_fields": ["model", "temperature", "max_tokens", "messages"],
                "rerank_fields": ["model", "query", "documents", "top_n"],
            },
            "response_format_json_object": "N/A: documented by DeepSeek upstream, not tested on organizer proxy",
            "thinking": "N/A: documented by DeepSeek upstream, not tested on organizer proxy",
            "reasoning_effort": "N/A: documented by DeepSeek upstream, not tested on organizer proxy",
            "rerank_instruction_field": "N/A: not present in the confirmed organizer request example; not sent",
            "defaults_changed": False,
        },
        "documentation_sources": {
            "deepseek_json_output": "https://api-docs.deepseek.com/guides/json_mode/",
            "deepseek_thinking_mode": "https://api-docs.deepseek.com/guides/thinking_mode/",
            "deepseek_chat_completions": "https://api-docs.deepseek.com/api/create-chat-completion/",
            "qwen3_reranker_model_card": "https://huggingface.co/Qwen/Qwen3-Reranker-8B",
            "scope_note": "Upstream documentation does not establish organizer-proxy compatibility.",
        },
        "reranker_variants": [
            {
                "name": "baseline_semantic_query_text_only",
                "query": "analysis semantic query",
                "document": "chunk text",
                "uses_only_confirmed_fields": True,
            },
            {
                "name": "original_query_with_metadata_prefix",
                "query": "original user query",
                "document": "[document=<id>] [section=<id>] [date=<date>] chunk text",
                "uses_only_confirmed_fields": True,
            },
            {
                "name": "analysis_query_with_metadata_prefix",
                "query": "analysis semantic query",
                "document": "[document=<id>] [section=<id>] [date=<date>] chunk text",
                "uses_only_confirmed_fields": True,
            },
            {
                "name": "instruction_prefixed_analysis_query",
                "query": "<candidate instruction>\n<analysis semantic query>",
                "document": "[document=<id>] [section=<id>] [date=<date>] chunk text",
                "uses_only_confirmed_fields": True,
                "note": "Instruction is query text, not an unconfirmed API parameter.",
            },
        ],
        "real_measurements": {
            "retrieval_query_source": retrieval["query_source"],
            "local_bm25_document": retrieval["local_bm25_document"],
            "local_bm25_section": retrieval["local_bm25_section"],
            "candidate_reranker_recall@8": None,
            "candidate_reranker_ndcg@8": None,
            "claim_correctness": None,
            "fact_completeness": None,
            "verification_errors": None,
            "refusals": None,
            "p50_ms": None,
            "p95_ms": None,
            "api_calls": None,
            "na_reason": "No matched live or replayed model responses for baseline and candidate prompts.",
        },
        "variant_comparison": [
            {
                "metric": metric,
                "baseline": None,
                "candidate": None,
                "change": None,
                "reason": "N/A: no matched live or replayed responses under both prompt variants",
            }
            for metric in (
                "reranker_recall@8",
                "reranker_ndcg@8",
                "claim_correctness",
                "fact_completeness",
                "verification_error_rate",
                "refusal_rate",
                "p50_ms",
                "p95_ms",
                "api_calls",
            )
        ],
        "simulated_contract_checks": {
            "role": "Parser/schema fixtures only; not LLM quality measurements.",
            "passed": sum(item["passed"] for item in simulated),
            "total": len(simulated),
            "cases": simulated,
        },
        "decision": "Keep v2 prompts and current reranker payload as runtime defaults until matched real or replayed measurements show improvement.",
    }


def _validate_case(case: dict[str, Any]) -> dict[str, Any]:
    payload = case["input"]
    output = case["expected_output"]
    error = None
    try:
        _validate_verification(output, payload["generation"], payload["evidence"])
    except Exception as exc:  # surfaced in the report, not hidden
        error = f"{type(exc).__name__}: {exc}"
    expected_claim_ids = {row["claim_id"] for row in payload["generation"]["claims"]}
    checked_claim_ids = {row["claim_id"] for row in output["claim_checks"]}
    compatible = error is None and (
        output["verdict"] != "pass" or expected_claim_ids == checked_claim_ids
    )
    return {
        "id": case["id"],
        "passed": compatible,
        "verdict": output["verdict"],
        "statuses": [row["status"] for row in output["claim_checks"]],
        "parser_error": error,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_markdown(report: dict[str, Any]) -> str:
    real = report["real_measurements"]
    checks = report["simulated_contract_checks"]
    lines = [
        "# Prompt experiment report",
        "",
        "The runtime still uses the baseline v2 prompts. Candidate prompts are not connected.",
        "",
        "## Real offline measurements",
        "",
        "| Metric | Value | Denominator / reason |",
        "|---|---:|---|",
        f"| BM25 document Recall@8 | {real['local_bm25_document']['@8']['recall']:.6f} | {real['local_bm25_document']['question_denominator']} questions |",
        f"| BM25 document nDCG@8 | {real['local_bm25_document']['@8']['ndcg']:.6f} | {real['local_bm25_document']['question_denominator']} questions |",
        f"| BM25 section Recall@8 | {real['local_bm25_section']['@8']['recall']:.6f} | {real['local_bm25_section']['question_denominator']} questions / {real['local_bm25_section']['label_denominator']} labels |",
        f"| BM25 section nDCG@8 | {real['local_bm25_section']['@8']['ndcg']:.6f} | {real['local_bm25_section']['question_denominator']} questions |",
        f"| Candidate reranker / claims / verification / latency | N/A | {real['na_reason']} |",
        "",
        "These BM25 figures use original questions and are shared context, not evidence that either prompt variant is better.",
        "",
        "Candidate design: atomic requested facts and claims; explicit version/date/identifier checks; every verification claim checked once with unchanged evidence IDs and a nonempty reason.",
        "",
        "## Baseline versus candidate",
        "",
        "| Metric | Baseline | Candidate | Change |",
        "|---|---:|---:|---:|",
    ]
    for item in report["variant_comparison"]:
        lines.append(f"| {item['metric']} | N/A | N/A | N/A |")
    lines.extend(
        [
            "",
            "All variant comparisons are N/A because no matched live or replayed outputs exist.",
            "",
            "## Simulated parser-contract checks",
            "",
            f"Passed: {checks['passed']}/{checks['total']}. These are fixtures, not LLM measurements.",
            "",
            "| Case | Verdict | Statuses | Parser compatible |",
            "|---|---|---|:---:|",
        ]
    )
    for case in checks["cases"]:
        lines.append(
            f"| {case['id']} | {case['verdict']} | {', '.join(case['statuses']) or 'none'} | {'yes' if case['passed'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            "## API capability conclusion",
            "",
            "`response_format=json_object`, `thinking`, `reasoning_effort`, and a separate reranker `instruction` remain disabled by default because organizer-proxy compatibility was not measured.",
            "",
            f"Decision: {report['decision']}",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
