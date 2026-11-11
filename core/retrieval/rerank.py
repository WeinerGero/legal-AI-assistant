"""Переранжирование Qwen3 Reranker 8B (ARCHITECTURE.md §7.5).

Адаптер получает обычный текст запроса и каждого кандидата, возвращает
индексы входного массива. Внутренний контракт ядра не совпадает
с транспортным форматом прокси ``POST /rerank`` — восстановление
``chunk_id`` выполняет реализация порта ``Reranker``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from core.contracts import Reranker


def rerank_candidates(
    reranker: Reranker,
    *,
    query: str,
    candidates: Sequence[tuple[UUID, str]],
    top_n: int = 10,
) -> list[tuple[UUID, float | None]]:
    """Возвращает ``(chunk_id, score)`` в порядке убывания релевантности.

    Отсутствие числовой оценки не считается ошибкой: прокси может
    возвращать только порядок.
    """
    documents = [text for _chunk_id, text in candidates]
    ranked = reranker.rerank(query, documents, top_n)
    result: list[tuple[UUID, float | None]] = []
    for index, score in ranked:
        if not 0 <= index < len(candidates):
            raise ValueError(f"индекс переранжирования вне границ: {index}")
        result.append((candidates[index][0], score))
    return result
