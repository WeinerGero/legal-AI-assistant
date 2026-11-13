"""Программные проверки перед вызовом DeepSeek №3 (§9.1).

Проверяется форма и целостность ссылок, но **не** правильность вывода.
Наличие корректного ``evidence_id`` не доказывает юридическую верность.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import Evidence, GenerationResultV1, VerificationResultV1


@dataclass(slots=True)
class ProgrammaticReport:
    """Итог формальных проверок."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_result(
    result: GenerationResultV1,
    *,
    registry: dict[str, Evidence],
    max_answer_chars: int = 10_000,
) -> ProgrammaticReport:
    """Валидность JSON, уникальность ID, доказательства, границы длин."""
    report = ProgrammaticReport()

    block_ids: set[str] = set()
    claim_ids: set[str] = set()
    for block in result.blocks:
        if block.block_id in block_ids:
            report.errors.append(f"повторяющийся block_id: {block.block_id}")
        block_ids.add(block.block_id)

        if not block.segments:
            report.errors.append(f"пустой блок: {block.block_id}")

        for segment in block.segments:
            if segment.claim_id in claim_ids:
                report.errors.append(f"повторяющийся claim_id: {segment.claim_id}")
            claim_ids.add(segment.claim_id)

            if segment.claim_type in {"legal_fact", "legal_inference", "qualification"}:
                if not segment.evidence_ids:
                    report.errors.append(f"{segment.claim_id}: нет доказательств")
                for evidence_id in segment.evidence_ids:
                    if evidence_id not in registry:
                        report.errors.append(
                            f"{segment.claim_id}: неизвестный evidence_id {evidence_id}"
                        )

    total_chars = sum(
        len(segment.text) for block in result.blocks for segment in block.segments
    )
    if total_chars > max_answer_chars:
        report.errors.append(f"итоговый текст длиннее {max_answer_chars} символов")

    return report


def check_evidence_integrity(registry: dict[str, Evidence]) -> ProgrammaticReport:
    """Каждый ``evidence_id`` должен описывать один точный диапазон одной редакции."""
    report = ProgrammaticReport()
    for evidence_id, record in registry.items():
        if record.evidence_id != evidence_id:
            report.errors.append(f"несоответствие ключа реестра: {evidence_id}")
        if not record.text:
            report.errors.append(f"{evidence_id}: пустой текст основания")
        if record.start_char is not None and record.end_char is not None:
            if record.end_char <= record.start_char:
                report.errors.append(f"{evidence_id}: неверный диапазон источника")
    return report


def check_verification_coverage(
    generation: GenerationResultV1, verification: VerificationResultV1
) -> ProgrammaticReport:
    """Проверяет, что модель №3 оценила каждое юридическое утверждение ровно один раз."""
    report = ProgrammaticReport()
    expected = {
        segment.claim_id
        for block in generation.blocks
        for segment in block.segments
        if segment.claim_type in {"legal_fact", "legal_inference", "qualification"}
    }
    actual = [check.claim_id for check in verification.claim_checks]
    duplicates = sorted({claim_id for claim_id in actual if actual.count(claim_id) > 1})
    missing = sorted(expected - set(actual))
    unknown = sorted(set(actual) - expected)
    for claim_id in duplicates:
        report.errors.append(f"повторная проверка claim_id: {claim_id}")
    for claim_id in missing:
        report.errors.append(f"не проверен claim_id: {claim_id}")
    for claim_id in unknown:
        report.errors.append(f"проверен неизвестный claim_id: {claim_id}")
    if expected and not actual:
        report.errors.append("модель проверки вернула пустой claim_checks")
    return report
