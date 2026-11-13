"""Оркестрация поиска (ARCHITECTURE.md §7.1–7.4).

Порядок:

1. SQL-поиск точных реквизитов — обязательные кандидаты.
2. BM25 по лексическим формулировкам ``AnalysisResultV1.search_queries``.
3. Векторный поиск по смысловым формулировкам.
4. RRF по двум каналам.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from core.contracts import (
    ChunkRef,
    DocumentRepository,
    EmbeddingProvider,
    LexicalSearch,
    VectorSearch,
)
from core.fusion import merge_within_channel, reciprocal_rank_fusion


@dataclass(slots=True)
class SearchOutcome:
    """Кандидаты до переранжирования."""

    mandatory: list[ChunkRef]
    ranked: list[tuple[UUID, float]]


class HybridRetriever:
    """Связывает четыре порта в один поисковый проход."""

    def __init__(
        self,
        documents: DocumentRepository,
        lexical: LexicalSearch,
        vectors: VectorSearch,
        embeddings: EmbeddingProvider,
        *,
        top_k: int = 30,
        top_k_vector: int | None = None,
        rrf_k: int = 60,
    ) -> None:
        self._documents = documents
        self._lexical = lexical
        self._vectors = vectors
        self._embeddings = embeddings
        self._top_k_lexical = top_k
        self._top_k_vector = top_k if top_k_vector is None else top_k_vector
        self._rrf_k = rrf_k

    def search(
        self,
        *,
        generation_id: UUID,
        lexical_queries: list[str],
        semantic_queries: list[str],
        contract_number: str | None = None,
        counterparty: str | None = None,
    ) -> SearchOutcome:
        mandatory = list(
            self._documents.chunks_by_exact_reference(
                generation_id,
                contract_number=contract_number,
                counterparty=counterparty,
            )
        )

        # Пустая формулировка после нормализации ничего не ищет и только
        # размывает ранжирование (§7.2: запросы приходят из нормализации).
        lexical = _non_blank(lexical_queries)
        semantic = _non_blank(semantic_queries)

        lexical_lists = [
            self._lexical.search(generation_id, q, self._top_k_lexical) for q in lexical
        ]
        semantic_lists: list[list[tuple[UUID, float]]] = []
        vectors = self._embeddings.embed(semantic) if semantic else []
        if len(vectors) != len(semantic):
            raise ValueError("число эмбеддингов не совпадает с числом запросов")
        for query, vector in zip(semantic, vectors, strict=True):
            if len(vector) != self._embeddings.dimension:
                raise ValueError(
                    f"неверная размерность эмбеддинга для {query!r}: "
                    f"{len(vector)} вместо {self._embeddings.dimension}"
                )
            semantic_lists.append(
                list(self._vectors.search(generation_id, vector, self._top_k_vector))
            )

        ranked = reciprocal_rank_fusion(
            {
                "bm25": merge_within_channel(lexical_lists),
                "vector": merge_within_channel(semantic_lists),
            },
            k=self._rrf_k,
        )
        return SearchOutcome(mandatory=mandatory, ranked=ranked)


def _non_blank(queries: list[str]) -> list[str]:
    """Отбрасывает пустые формулировки, сохраняя порядок и дубликаты."""
    return [query for query in queries if query.strip()]
