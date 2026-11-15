"""Сервер цитат: ``evidence_id`` → ``answer_source_id`` (§10.1–10.3).

Модуль не угадывает смысл предложений: он переносит проверенные связи
в постоянные идентификаторы ``answer_source`` и назначает ``[1]``, ``[2]``
в порядке первого использования.
"""

from __future__ import annotations

from uuid import UUID

from core.contracts import (
    Citation,
    Evidence,
    GenerationResultV1,
    RenderedAnswerV1,
    RenderedBlock,
    RenderedSegment,
    RenderedSource,
)


def build_rendered_answer(
    *,
    answer_id: str,
    generation: GenerationResultV1,
    registry: dict[str, Evidence],
    evidence_source_ids: dict[str, str],
    verification_status: str,
) -> RenderedAnswerV1:
    """Формирует ``RenderedAnswerV1`` без смысловых преобразований текста."""
    numbers: dict[str, int] = {}
    sources: dict[str, RenderedSource] = {}
    blocks: list[RenderedBlock] = []

    for block in generation.blocks:
        segments: list[RenderedSegment] = []
        for segment in block.segments:
            citations: list[Citation] = []
            for evidence_id in segment.evidence_ids:
                source_id = evidence_source_ids.get(evidence_id)
                if source_id is None:
                    continue
                number = numbers.setdefault(source_id, len(numbers) + 1)
                citations.append(Citation(answer_source_id=source_id, number=number))
                if source_id not in sources:
                    record = registry[evidence_id]
                    sources[source_id] = RenderedSource(
                        answer_source_id=source_id,
                        number=number,
                        document_title=record.document_title,
                        version_label=record.version_label,
                        section_number=record.section,
                    )
            segments.append(
                RenderedSegment(
                    claim_id=segment.claim_id,
                    text=segment.text,
                    citations=citations,
                )
            )
        blocks.append(RenderedBlock(block_type=block.block_type, segments=segments))

    return RenderedAnswerV1(
        answer_id=answer_id,
        legal_status=generation.legal_status,
        verification_status=verification_status,
        blocks=blocks,
        sources=sorted(sources.values(), key=lambda item: item.number),
        missing_information=generation.missing_information,
        clarification_question=generation.clarification_question,
    )


def source_chain_is_owned(answer_id: UUID, owner_user_id: UUID, answer_owner: UUID) -> bool:
    """Цепочка ``answer_source → answer → message → conversation → owner`` (§2.3)."""
    return owner_user_id == answer_owner
