"""Каналы поиска и RRF (ARCHITECTURE.md §7.2–7.4).

Модуль связывает порты ``LexicalSearch`` и ``VectorSearch`` и не знает,
какое хранилище используется: pgvector, FAISS или PostgreSQL full-text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

RankedList = Sequence[tuple[UUID, float]]


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, RankedList], *, k: int = 60
) -> list[tuple[UUID, float]]:
    """RRF по двум каналам BM25 и Vector (ARCHITECTURE.md §7.4).

    Отсутствующий в списке фрагмент не получает вклада от этого списка.
    Точные кандидаты SQL в пул RRF не добавляются — они обязательны отдельно.
    """
    scores: dict[UUID, float] = {}
    for ranked in ranked_lists.values():
        for position, (chunk_id, _score) in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], str(item[0])))


def merge_within_channel(formulations: Iterable[RankedList]) -> RankedList:
    """Несколько формулировок одного канала объединяются по лучшему рангу."""
    best: dict[UUID, tuple[int, float]] = {}
    for ranked in formulations:
        for position, (chunk_id, score) in enumerate(ranked):
            current = best.get(chunk_id)
            if current is None or position < current[0]:
                best[chunk_id] = (position, score)
    ordered = sorted(best.items(), key=lambda item: (item[1][0], str(item[0])))
    return [(chunk_id, value[1]) for chunk_id, value in ordered]
