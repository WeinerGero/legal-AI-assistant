"""Построение ``GenerationRequestV1`` и разбор ``GenerationResultV1`` (§8).

Ответ модели разбит на блоки и сегменты; каждый юридически значимый сегмент
несёт ``evidence_id`` из реестра текущего запроса.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from core.contracts import (
    AnalysisResultV1,
    Block,
    Evidence,
    GenerationRequestV1,
    GenerationResultV1,
    Segment,
    TemporalContext,
)

GRANULAR_CLAIM_TYPES = frozenset({"legal_fact", "legal_inference", "qualification"})


def build_generation_request(
    analysis: AnalysisResultV1,
    evidence: list[Evidence],
    *,
    event_date: date | None = None,
    applicability_unknown: bool = False,
) -> GenerationRequestV1:
    """Вход DeepSeek №2: самостоятельный вопрос и реестр доказательств."""
    return GenerationRequestV1(
        question=analysis.standalone_question,
        requested_facts=analysis.requested_facts,
        temporal_context=TemporalContext(
            event_date=event_date,
            applicability_unknown=applicability_unknown,
        ),
        evidence=evidence,
    )


def iter_segments(result: GenerationResultV1):
    """Проходит все сегменты ответа в порядке блоков и позиций."""
    for block in result.blocks:
        for segment in block.segments:
            yield block, segment


def claims_needing_evidence(result: GenerationResultV1) -> list[Segment]:
    """Юридически значимые сегменты, у которых пуст ``evidence_ids``."""
    return [
        segment
        for _block, segment in iter_segments(result)
        if segment.claim_type in GRANULAR_CLAIM_TYPES and not segment.evidence_ids
    ]


def unknown_evidence_ids(result: GenerationResultV1, known: set[str]) -> list[str]:
    """``evidence_id``, отсутствующие в реестре текущего запуска."""
    return sorted(
        {
            evidence_id
            for _block, segment in iter_segments(result)
            for evidence_id in segment.evidence_ids
            if evidence_id not in known
        }
    )


def collect_used_evidence(
    result: GenerationResultV1, registry: dict[str, Evidence]
) -> list[Evidence]:
    """Точные основания, реально использованные в ответе, для проверки №3."""
    used: list[Evidence] = []
    seen: set[str] = set()
    for _block, segment in iter_segments(result):
        for evidence_id in segment.evidence_ids:
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            if evidence_id in registry:
                used.append(registry[evidence_id])
    return used


def blocks_by_type(result: GenerationResultV1, block_type: str) -> list[Block]:
    return [block for block in result.blocks if block.block_type == block_type]


def claim_index(result: GenerationResultV1) -> dict[str, Segment]:
    """Карта ``claim_id`` → сегмент; используется проверкой и цитированием."""
    return {segment.claim_id: segment for _block, segment in iter_segments(result)}


def resolve_answer_id(request_id: UUID) -> str:
    """Постоянный идентификатор ответа создаётся сервером, не моделью."""
    return str(request_id)
