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

# Больше запрошенного ``top_n`` прокси не обязан отдавать; меньше — допустимо.
MAX_RESULTS_TOP_N = 10


def rerank_candidates(
    reranker: Reranker,
    *,
    query: str,
    candidates: Sequence[tuple[UUID, str]],
    top_n: int = MAX_RESULTS_TOP_N,
) -> list[tuple[UUID, float | None]]:
    """Возвращает ``(chunk_id, score)`` в порядке убывания релевантности.

    Отсутствие числовой оценки не считается ошибкой: прокси может
    возвращать только порядок (§7.5). Ответ прокси проверяется на границы
    и уникальность индексов: повторный или отсутствующий индекс — дефект
    адаптера, а не повод для частичного результата.
    """
    if top_n <= 0 or not candidates:
        return []
    documents = [text for _chunk_id, text in candidates]
    ranked = reranker.rerank(query, documents, top_n)

    seen_indexes: set[int] = set()
    seen_chunks: set[UUID] = set()
    result: list[tuple[UUID, float | None]] = []
    for index, score in ranked:
        if not 0 <= index < len(candidates):
            raise ValueError(f"индекс переранжирования вне границ: {index}")
        if index in seen_indexes:
            raise ValueError(f"индекс переранжирования повторяется: {index}")
        seen_indexes.add(index)
        chunk_id = candidates[index][0]
        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        result.append((chunk_id, score))
    return result
