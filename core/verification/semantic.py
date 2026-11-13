"""Смысловая проверка через DeepSeek №3 и интерпретация вердикта (§9.2–9.5)."""

from __future__ import annotations

from core.contracts import (
    Evidence,
    GenerationResultV1,
    VerificationRequestV1,
    VerificationResultV1,
)
from core.generation.answer import collect_used_evidence


def build_verification_request(
    *,
    question: str,
    requested_facts: list[str],
    generation: GenerationResultV1,
    registry: dict[str, Evidence],
) -> VerificationRequestV1:
    """Передаёт полный ответ и только реально использованные основания."""
    return VerificationRequestV1(
        question=question,
        requested_facts=requested_facts,
        generation=generation,
        evidence=collect_used_evidence(generation, registry),
    )


def is_pass(
    result: VerificationResultV1, generation: GenerationResultV1 | None = None
) -> bool:
    """``pass`` только при полной поддержке всех утверждений (§9.5)."""
    if result.verdict != "pass":
        return False
    if result.missing_requested_facts:
        return False
    checks = result.claim_checks
    if generation is not None:
        expected = {
            segment.claim_id
            for block in generation.blocks
            for segment in block.segments
            if segment.claim_type in {"legal_fact", "legal_inference", "qualification"}
        }
        actual = [check.claim_id for check in checks]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            return False
        if not expected:
            return not actual
    return bool(checks) and all(check.status == "supported" for check in checks)


def summarize(result: VerificationResultV1) -> dict[str, int]:
    """Счётчики статусов для метрик и журнала, без пользовательского текста."""
    counts: dict[str, int] = {}
    for check in result.claim_checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts
