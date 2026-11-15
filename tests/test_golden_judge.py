from __future__ import annotations

import pytest

from submission.golden_judge import validate_result


def _valid_result() -> dict:
    return {
        "case_id": "H1",
        "criterion_scores": {
            "legal_correctness": 1.0,
            "completeness": 0.75,
            "evidence_and_citations": 1.0,
            "expected_behavior": 1.0,
            "relevance_and_conciseness": 0.75,
            "refusal_and_uncertainty": 1.0,
        },
        "overall_score": 0.925,
        "critical_failure": False,
        "missing_facts": [],
        "unsupported_claims": [],
        "incorrect_citations": [],
        "failure_conditions_triggered": [],
        "explanation": "The supplied independent labels support the score.",
        "suspected_failure_stage": None,
    }


def test_judge_schema_accepts_consistent_weighted_score() -> None:
    validate_result(_valid_result(), expected_case_id="H1")


def test_invalid_judge_output_is_an_evaluation_error_not_zero() -> None:
    result = _valid_result()
    result["criterion_scores"]["legal_correctness"] = 0.6
    with pytest.raises(ValueError, match="allowed scale"):
        validate_result(result, expected_case_id="H1")

