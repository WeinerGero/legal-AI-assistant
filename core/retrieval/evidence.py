"""Отбор доказательств и присвоение ``evidence_id`` (ARCHITECTURE.md §7.6).

Правила:

- обязательные по реквизитам положения добавляются всегда;
- добавляются соседние пункты, определяющие срок, исключение или термин;
- применимые дополнительные соглашения и противоречащие положения;
- для каждого ``requested_fact`` проверяется достаточность набора;
- один ``evidence_id`` описывает один точный диапазон одной редакции.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from core.contracts import ChunkRef, Evidence


def assign_evidence_ids(records: Sequence[Evidence]) -> list[Evidence]:
    """Перенумеровывает реестр в ``E001``, ``E002``… детерминированно."""
    return [
        item.model_copy(update={"evidence_id": f"E{position:03d}"})
        for position, item in enumerate(records, start=1)
    ]


def missing_facts(
    requested_facts: Sequence[str],
    covered_facts: Sequence[str],
) -> list[str]:
    """Возвращает запрошенные факты, для которых нет основания в наборе.

    Если вопрос требует одновременно срока и точки начала отсчёта,
    наличие только срока недостаточно.
    """
    covered = {fact.strip().lower() for fact in covered_facts}
    return [fact for fact in requested_facts if fact.strip().lower() not in covered]


def split_multi_clause_chunk(chunk: ChunkRef) -> list[UUID]:
    """Возвращает отдельные ``clause_id`` фрагмента, пересекающего границы.

    Модель не получает право ссылаться на весь многочастный фрагмент
    как на единый юридический пункт.
    """
    return list(chunk.clause_ids)
