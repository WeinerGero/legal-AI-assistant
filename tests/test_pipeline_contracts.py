from __future__ import annotations

import pytest

from submission.pipeline import (
    _align_generation_citations,
    _normalize_verification,
    _validate_generation,
    _validate_verification,
)


EVIDENCE = [
    {
        "evidence_id": "E001",
        "document": "Договор А-08/2024",
        "section": "4.1",
        "text": "Арендная плата составляет 450 000 рублей.",
    },
    {
        "evidence_id": "E002",
        "document": "Договор А-08/2024",
        "section": "4.2",
        "text": "Плата вносится до 5-го числа месяца.",
    },
]


def test_structured_claims_are_rendered_with_matching_markers() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Плата составляет 450 000 рублей. Внести её нужно до 5-го числа.",
        "missing_facts": [],
        "claims": [
            {"claim_id": "C001", "text": "Плата составляет 450 000 рублей.", "evidence_ids": ["E001"]},
            {"claim_id": "C002", "text": "Внести её нужно до 5-го числа.", "evidence_ids": ["E002"]},
        ],
    }
    _align_generation_citations(generation)
    _validate_generation(generation, EVIDENCE)
    assert generation["answer"].splitlines() == [
        "Плата составляет 450 000 рублей. [E001]",
        "Внести её нужно до 5-го числа. [E002]",
    ]


def test_pass_with_unanswered_facts_is_rejected() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Плата составляет 450 000 рублей. [E001]",
        "missing_facts": [],
        "claims": [
            {"claim_id": "C001", "text": "Плата составляет 450 000 рублей.", "evidence_ids": ["E001"]}
        ],
    }
    verification = {
        "verdict": "pass",
        "claim_checks": [
            {"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "Подтверждено"}
        ],
        "unanswered_facts": ["срок внесения"],
        "issues": [],
    }
    with pytest.raises(ValueError, match="unanswered_facts"):
        _validate_verification(verification, generation, EVIDENCE)


def test_inconsistent_pass_is_safely_downgraded_for_revision() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Плата составляет 450 000 рублей. [E001]",
        "missing_facts": [],
        "claims": [
            {"claim_id": "C001", "text": "Плата составляет 450 000 рублей.", "evidence_ids": ["E001"]}
        ],
    }
    verification = {
        "verdict": "pass",
        "claim_checks": [
            {"claim_id": "C999", "status": "supported", "evidence_ids": ["E002"], "reason": "wrong claim"},
            {"claim_id": "C001", "status": "supported", "evidence_ids": ["E002"], "reason": "wrong evidence"},
        ],
        "unanswered_facts": [],
        "issues": [],
    }
    _normalize_verification(verification, generation)
    assert verification["verdict"] == "revision_required"
    assert verification["claim_checks"] == [
        {
            "claim_id": "C001",
            "status": "unsupported",
            "evidence_ids": ["E001"],
            "reason": "Verifier changed the claim evidence mapping; revision is required.",
        }
    ]

