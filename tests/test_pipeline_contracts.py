from __future__ import annotations

import json

import pytest

from submission.pipeline import (
    SubmissionPipeline,
    _append_follow_up_question,
    _valid_follow_up_question,
    _align_generation_citations,
    _fact_requirements,
    _normalize_verification,
    _normalize_presentation_blocks,
    _render_presentation_blocks,
    _render_verified_partial_answer,
    _revision_scope,
    _select_evidence_records,
    _validate_decimal_calculations,
    _validate_generation,
    _validate_public_answer,
    _validate_verification,
)


def test_verifier_retries_missing_verdict_and_keeps_claim_validation() -> None:
    valid = {
        "verdict": "pass",
        "claim_checks": [{
            "claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "Пункт подтверждает срок.",
        }],
        "fact_checks": [], "unanswered_facts": [], "issues": [], "follow_up_question": None,
        "presentation_blocks": [{
            "block_type": "conclusion",
            "segments": [{"text": "Срок составляет пять дней.", "claim_ids": ["C001"], "evidence_ids": ["E001"]}],
        }],
    }

    class Client:
        budget = None
        metrics = None

        def __init__(self) -> None:
            self.calls: list[str] = []

        def complete_json(self, prompt: str, payload: str, *, stage: str) -> str:
            self.calls.append(stage)
            if len(self.calls) == 1:
                return json.dumps({"claim_checks": valid["claim_checks"]}, ensure_ascii=False)
            assert "format_correction" in json.loads(payload)
            return json.dumps(valid, ensure_ascii=False)

    pipeline = SubmissionPipeline.__new__(SubmissionPipeline)
    pipeline.client = Client()
    pipeline.verification_prompt = "Verify every claim."
    pipeline._stage_timings = {}
    result = pipeline._verify(
        [], {"target_language": "ru", "standalone_question": "Какой срок?", "requested_facts": []},
        {"answer_type": "answer", "claims": [{"claim_id": "C001", "text": "Срок составляет пять дней.", "evidence_ids": ["E001"]}]},
        [{"evidence_id": "E001", "text": "Срок составляет пять дней."}],
    )
    assert result["verdict"] == "pass"
    assert pipeline.client.calls == ["verification", "verification_format_retry"]


def test_verifier_retries_unusable_presentation_without_dropping_verified_claims() -> None:
    generation = {"answer_type": "answer", "claims": [{
        "claim_id": "C001", "text": "Срок составляет пять дней.", "evidence_ids": ["E001"],
    }]}
    valid = {
        "verdict": "pass",
        "claim_checks": [{"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "Пункт подтверждает срок."}],
        "fact_checks": [], "unanswered_facts": [], "issues": [], "follow_up_question": None,
        "presentation_blocks": [{"block_type": "conclusion", "segments": [{
            "text": "Оплату нужно внести в течение пяти дней.", "claim_ids": ["C001"], "evidence_ids": ["E001"],
        }]}],
    }

    class Client:
        budget = None
        metrics = None

        def __init__(self) -> None:
            self.calls = 0

        def complete_json(self, prompt: str, payload: str, *, stage: str) -> str:
            self.calls += 1
            return json.dumps({**valid, "presentation_blocks": []} if self.calls == 1 else valid, ensure_ascii=False)

    pipeline = SubmissionPipeline.__new__(SubmissionPipeline)
    pipeline.client = Client()
    pipeline.verification_prompt = "Verify every claim."
    pipeline._stage_timings = {}
    result = pipeline._verify(
        [], {"target_language": "ru", "standalone_question": "Какой срок?", "requested_facts": []},
        generation, [{"evidence_id": "E001", "text": "Срок составляет пять дней."}],
    )
    assert pipeline.client.calls == 2
    assert result["presentation_blocks"][0]["segments"][0]["text"] == "Оплату нужно внести в течение пяти дней."


def test_presentation_blocks_keep_supported_claims_and_render_connected_text() -> None:
    generation = {
        "claims": [
            {"claim_id": "C001", "text": "Оплата составляет 100 рублей.", "evidence_ids": ["E001"]},
            {"claim_id": "C002", "text": "Срок оплаты — пять дней.", "evidence_ids": ["E002"]},
        ]
    }
    verification = {
        "claim_checks": [
            {"claim_id": "C001", "status": "supported"},
            {"claim_id": "C002", "status": "supported"},
        ],
        "presentation_blocks": [{"block_type": "conclusion", "segments": [{
            "text": "Оплата составляет 100 рублей, её нужно внести в течение пяти дней.",
            "claim_ids": ["C001", "C002"], "evidence_ids": ["E001", "E002"],
        }]}],
    }
    evidence = [
        {"evidence_id": "E001", "document": "contract", "section": "1"},
        {"evidence_id": "E002", "document": "contract", "section": "2"},
    ]
    _normalize_presentation_blocks(verification, generation, evidence)
    assert _render_presentation_blocks(verification, generation, evidence, target_language="ru") == (
        "Оплата составляет 100 рублей, её нужно внести в течение пяти дней. [1] [2]\n\n"
        "Источники:\n[1] contract — 1\n[2] contract — 2"
    )


def test_follow_up_question_is_safe_and_single():
    verification = {"follow_up_question": "Уточните дату вступления редакции в силу?"}
    generation = {"claims": [{"claim_id": "C1"}]}
    answer = _append_follow_up_question("Подтверждённая часть ответа.", verification, generation, target_language="ru", answer_state="success")
    assert answer.endswith("Уточните дату вступления редакции в силу?")
    assert _append_follow_up_question(answer, verification, generation, target_language="ru", answer_state="success") == answer


def test_follow_up_question_rejects_generic_markers_and_refusal():
    generation = {"claims": []}
    assert not _valid_follow_up_question("Чем ещё помочь?", "ru")
    assert not _valid_follow_up_question("Уточните [E001]?", "ru")
    assert _append_follow_up_question("Недостаточно данных.", {"follow_up_question": "Уточните дату?"}, generation, target_language="ru", answer_state="legal_refusal") == "Недостаточно данных."


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


def test_refusal_drops_bare_evidence_ids_without_inventing_claims() -> None:
    generation = {
        "answer_type": "insufficient_evidence",
        "answer": "Insufficient evidence E001.",
        "missing_facts": ["requested detail"],
        "claims": [],
    }
    _align_generation_citations(generation)
    _validate_generation(generation, EVIDENCE)
    assert "E001" not in generation["answer"]


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


def test_fact_check_schema_aliases_are_normalized_without_promoting_missing_facts() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Подтверждено. [E001]",
        "missing_facts": [],
        "claims": [{"claim_id": "C001", "text": "Подтверждено.", "evidence_ids": ["E001"]}],
    }
    verification = {
        "verdict": "pass",
        "claim_checks": [{"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "ok"}],
        "fact_checks": [
            {"fact_id": "F001", "status": "supported", "claim_ids": ["C001"], "reason": "ok"},
            {"fact_id": "F002", "status": "unanswered", "claim_ids": ["C999"], "reason": "missing"},
        ],
        "unanswered_facts": [],
        "issues": [],
    }
    requirements = _fact_requirements(["first", "second"])
    _normalize_verification(verification, generation, requirements)
    assert verification["fact_checks"][0]["status"] == "addressed"
    assert verification["fact_checks"][1]["status"] == "missing"
    assert verification["fact_checks"][1]["claim_ids"] == []
    assert verification["verdict"] == "revision_required"


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
        "unanswered_facts": ["F002: второй факт"],
        "issues": [],
    }
    rendered = _render_verified_partial_answer(generation, verification, EVIDENCE, target_language="ru")
    assert rendered is not None
    answer, retained = rendered
    assert retained == 1
    assert "Подтверждённый факт" in answer
    assert "Ошибочный факт" not in answer
    assert "Не подтверждено документами" in answer
    assert "F002" not in answer


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


@pytest.mark.parametrize(
    ("operation", "operands", "result"),
    [("min", ["150000", "100000"], "100000"), ("max", ["7", "12", "5"], "12")],
)
def test_decimal_calculation_supports_general_bounds(
    operation: str, operands: list[str], result: str
) -> None:
    _validate_decimal_calculations({
        "calculations": [{"operation": operation, "operands": operands, "result": result, "unit": "RUB"}]
    })


@pytest.mark.parametrize(
    "answer",
    [
        "Обычный ответ [1].",
        "Подтверждённая часть ответа. Не установлено: срок.",
        "В удовлетворении запроса следует отказать.",
        "Применяются пункты 3.1 и 3.4 двух документов.",
        "Для редакции 2023 года вывод ограничен доступным текстом.",
    ],
)
def test_public_answer_accepts_distinct_legal_outcomes(answer: str) -> None:
    _validate_public_answer(answer)


@pytest.mark.parametrize("marker", ["F001", "[E001]", "revision_required", "partially_supported"])
def test_public_answer_rejects_internal_markers(marker: str) -> None:
    with pytest.raises(ValueError, match="internal"):
        _validate_public_answer(f"Ответ: {marker}")


def test_revision_scope_revisits_dependent_claims_without_case_specific_rules() -> None:
    scope = _revision_scope(
        {
            "claim_checks": [{"claim_id": "C002", "status": "contradicted"}],
            "fact_checks": [{"fact_id": "F003", "status": "missing"}],
        },
        {"claims": [{"claim_id": "C001"}, {"claim_id": "C002"}]},
    )
    assert scope["defective_claim_ids"] == ["C002"]
    assert scope["unanswered_fact_ids"] == ["F003"]
    assert "calculation" in str(scope["instruction"])


def test_citation_alignment_normalizes_claimless_refusal() -> None:
    generation = {
        "answer": "Недостаточно данных [E001]",
        "claims": [],
        "missing_facts": ["условие"],
    }
    _align_generation_citations(generation)
    assert generation["answer_type"] == "insufficient_evidence"
    assert "E001" not in generation["answer"]


def test_missing_verdict_is_derived_conservatively() -> None:
    generation = {
        "answer_type": "answer",
        "answer": "Факт [E001]",
        "claims": [{"claim_id": "C001", "text": "Факт", "evidence_ids": ["E001"]}],
        "missing_facts": [],
    }
    verification = {
        "claim_checks": [{"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "подтверждено"}],
        "fact_checks": [{"fact_id": "F001", "status": "addressed", "claim_ids": ["C001"], "reason": "проверено"}],
        "unanswered_facts": [],
        "issues": [],
    }
    requirements = [{"fact_id": "F001", "description": "факт"}]
    _normalize_verification(verification, generation, requirements)
    assert verification["verdict"] == "pass"


def test_evidence_selection_preserves_fact_diversity_without_gold_labels() -> None:
    records = [
        {"evidence_id": "E001", "text": "общий договор срок оплаты", "section": "1", "document": "a"},
        {"evidence_id": "E002", "text": "договор лимит ответственности 50 процентов", "section": "2", "document": "a"},
        {"evidence_id": "E003", "text": "повторный договор срок оплаты", "section": "3", "document": "a"},
    ]
    selected = _select_evidence_records(
        records,
        [{"fact_id": "F001", "description": "срок оплаты"}, {"fact_id": "F002", "description": "лимит ответственности"}],
        2,
    )
    assert [item["evidence_id"] for item in selected] == ["E001", "E002"]

