"""Independent diagnostic judge; excluded from the competition ZIP allowlist."""

from __future__ import annotations

import json
from typing import Any

from submission.model_client import ModelClient

SCORES = {0.0, 0.25, 0.5, 0.75, 1.0}
WEIGHTS = {
    "legal_correctness": 0.25,
    "completeness": 0.20,
    "evidence_and_citations": 0.20,
    "expected_behavior": 0.15,
    "relevance_and_conciseness": 0.10,
    "refusal_and_uncertainty": 0.10,
}
REQUIRED_RESULT_FIELDS = {
    "case_id", "criterion_scores", "overall_score", "critical_failure",
    "missing_facts", "unsupported_claims", "incorrect_citations",
    "failure_conditions_triggered", "explanation", "suspected_failure_stage",
}

SYSTEM_PROMPT = """You are an independent diagnostic evaluator of a closed-corpus legal RAG answer.
Do not create an answer for the user. Assess only against the supplied golden fields and confirmed
verbatim corpus passages. Use criterion scores 0, 0.25, 0.50, 0.75, or 1.00. A critical legal
fabrication may set overall_score to zero only when it is verified against the supplied passages or
independent labels. If evidence is insufficient to decide, report the item as disputed rather than
inventing a failure. Return only the requested JSON object. This is an internal method, not the
organizer's closed judge."""


def evaluate_answer(
    client: ModelClient,
    case: dict[str, Any],
    answer: str,
    confirmed_passages: list[dict[str, Any]],
) -> dict[str, Any]:
    if not confirmed_passages:
        raise ValueError("judge requires independently confirmed corpus passages")
    payload = {
        "case_id": case["id"],
        "query": case["query"],
        "expected_behavior": case["expected_behavior"],
        "gold_answer": case["gold_answer"],
        "failure_conditions": case["failure_conditions"],
        "actual_answer": answer,
        "confirmed_passages": confirmed_passages,
        "criteria": WEIGHTS,
        "rubric": [0, 0.25, 0.5, 0.75, 1],
        "required_result_fields": sorted(REQUIRED_RESULT_FIELDS),
    }
    result = _parse(client.complete_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), stage="golden_judge"))
    validate_result(result, expected_case_id=str(case["id"]))
    return result


def compare_answers_blind(
    client: ModelClient,
    case: dict[str, Any],
    baseline_answer: str,
    candidate_answer: str,
    confirmed_passages: list[dict[str, Any]],
) -> dict[str, Any]:
    if not confirmed_passages:
        raise ValueError("pairwise judge requires independently confirmed corpus passages")
    candidate_scores: list[float] = []
    rounds: list[dict[str, Any]] = []
    for candidate_label, answer_a, answer_b in (
        ("B", baseline_answer, candidate_answer),
        ("A", candidate_answer, baseline_answer),
    ):
        payload = {
            "case_id": case["id"],
            "query": case["query"],
            "expected_behavior": case["expected_behavior"],
            "gold_answer": case["gold_answer"],
            "failure_conditions": case["failure_conditions"],
            "confirmed_passages": confirmed_passages,
            "answer_a": answer_a,
            "answer_b": answer_b,
            "output_schema": {
                "winner": "A|B|tie",
                "relative_score_for_b": "0|0.25|0.5|0.75|1",
                "critical_failure": "none|A|B|both",
                "explanation": "nonempty string",
            },
        }
        result = _parse(client.complete_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), stage="golden_pairwise_judge"))
        _validate_pairwise(result)
        score_for_b = float(result["relative_score_for_b"])
        candidate_scores.append(score_for_b if candidate_label == "B" else 1.0 - score_for_b)
        rounds.append(result)
    return {
        "case_id": str(case["id"]),
        "candidate_score": sum(candidate_scores) / 2,
        "rounds": rounds,
        "order_instability": abs(candidate_scores[0] - candidate_scores[1]),
    }


def validate_result(result: dict[str, Any], *, expected_case_id: str) -> None:
    if set(result) != REQUIRED_RESULT_FIELDS:
        raise ValueError("judge result fields do not match the schema")
    if str(result["case_id"]) != expected_case_id:
        raise ValueError("judge case_id mismatch")
    criteria = result["criterion_scores"]
    if not isinstance(criteria, dict) or set(criteria) != set(WEIGHTS):
        raise ValueError("judge criterion_scores do not match the rubric")
    if any(not isinstance(value, (int, float)) or float(value) not in SCORES for value in criteria.values()):
        raise ValueError("judge criterion score is outside the allowed scale")
    if not isinstance(result["critical_failure"], bool):
        raise ValueError("judge critical_failure must be boolean")
    for name in ("missing_facts", "unsupported_claims", "incorrect_citations", "failure_conditions_triggered"):
        if not isinstance(result[name], list):
            raise ValueError(f"judge {name} must be an array")
    if not isinstance(result["explanation"], str) or not result["explanation"].strip():
        raise ValueError("judge explanation is empty")
    calculated = 0.0 if result["critical_failure"] else sum(float(criteria[name]) * weight for name, weight in WEIGHTS.items())
    overall = result["overall_score"]
    if not isinstance(overall, (int, float)) or abs(float(overall) - calculated) > 1e-6:
        raise ValueError("judge overall_score is inconsistent with criteria")


def _parse(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("judge result must be an object")
    return result


def _validate_pairwise(result: dict[str, Any]) -> None:
    if set(result) != {"winner", "relative_score_for_b", "critical_failure", "explanation"}:
        raise ValueError("pairwise judge result fields do not match the schema")
    if result["winner"] not in {"A", "B", "tie"}:
        raise ValueError("pairwise judge winner is invalid")
    score = result["relative_score_for_b"]
    if not isinstance(score, (int, float)) or float(score) not in SCORES:
        raise ValueError("pairwise judge score is outside the allowed scale")
    expected_winner = "B" if score > 0.5 else "A" if score < 0.5 else "tie"
    if result["winner"] != expected_winner:
        raise ValueError("pairwise judge winner contradicts its score")
    if result["critical_failure"] not in {"none", "A", "B", "both"}:
        raise ValueError("pairwise judge critical_failure is invalid")
    if not isinstance(result["explanation"], str) or not result["explanation"].strip():
        raise ValueError("pairwise judge explanation is empty")
