from __future__ import annotations

import pytest

from submission.pipeline import (
    _align_generation_citations,
    _fact_requirements,
    _normalize_verification,
    _render_verified_partial_answer,
    _validate_decimal_calculations,
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


def test_refusal_drops_stray_evidence_markers_without_inventing_claims() -> None:
    generation = {
        "answer_type": "insufficient_evidence",
        "answer": "В документах нет подтверждённых сведений. [E001]",
        "missing_facts": ["запрошенные реквизиты"],
        "claims": [],
    }
    _align_generation_citations(generation)
    _validate_generation(generation, EVIDENCE)
    assert generation["answer"] == "В документах нет подтверждённых сведений."


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


@pytest.mark.parametrize(
    "facts",
    [
        ["общий порядок", "специальное условие"],
        ["срок продления", "наличие ускоренной процедуры"],
        ["формула", "исходные величины", "единица измерения"],
    ],
)
def test_pass_requires_an_explicit_check_for_every_atomic_fact(facts: list[str]) -> None:
    requirements = _fact_requirements(facts)
    generation = {
        "answer_type": "answer",
        "answer": "Подтверждённый факт. [E001]",
        "missing_facts": [],
        "claims": [{"claim_id": "C001", "text": "Подтверждённый факт.", "evidence_ids": ["E001"]}],
    }
    verification = {
        "verdict": "pass",
        "claim_checks": [{"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "подтверждено"}],
        "fact_checks": [{"fact_id": "F001", "status": "addressed", "claim_ids": ["C001"], "reason": "отвечено"}],
        "unanswered_facts": [],
        "issues": [],
    }
    _normalize_verification(verification, generation, requirements)
    assert verification["verdict"] == "revision_required"
    assert {row["fact_id"] for row in verification["fact_checks"]} == {
        row["fact_id"] for row in requirements
    }


def test_supported_independent_claim_survives_a_contradicted_claim() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Подтверждённый факт. [E001]\nОшибочный факт. [E002]",
        "missing_facts": ["второй факт"],
        "claims": [
            {"claim_id": "C001", "text": "Подтверждённый факт.", "evidence_ids": ["E001"]},
            {"claim_id": "C002", "text": "Ошибочный факт.", "evidence_ids": ["E002"]},
        ],
    }
    verification = {
        "verdict": "revision_required",
        "claim_checks": [
            {"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "подтверждено"},
            {"claim_id": "C002", "status": "contradicted", "evidence_ids": ["E002"], "reason": "противоречит"},
        ],
        "unanswered_facts": ["второй факт"],
        "issues": [],
    }
    rendered = _render_verified_partial_answer(generation, verification, EVIDENCE, target_language="ru")
    assert rendered is not None
    answer, retained = rendered
    assert retained == 1
    assert "Подтверждённый факт" in answer
    assert "Ошибочный факт" not in answer
    assert "Не подтверждено документами" in answer


@pytest.mark.parametrize(
    ("operands", "result"),
    [(["2400000", "13"], "312000"), (["600000", "15"], "90000"), (["10.25", "2"], "20.50")],
)
def test_decimal_calculation_is_entity_independent(operands: list[str], result: str) -> None:
    operation = "percent" if len(operands) == 2 and operands[1] in {"13", "15"} else "multiply"
    _validate_decimal_calculations({
        "calculations": [{"operation": operation, "operands": operands, "result": result, "unit": "RUB"}]
    })


def test_decimal_calculation_rejects_an_incorrect_result() -> None:
    with pytest.raises(ValueError, match="числовой результат"):
        _validate_decimal_calculations({
            "calculations": [{"operation": "percent", "operands": ["2400000", "13"], "result": "1", "unit": "RUB"}]
        })

