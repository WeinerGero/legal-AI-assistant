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

from core.contracts import ChunkRef, DocumentRepository, EmbeddingProvider, LexicalSearch, VectorSearch
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
        rrf_k: int = 60,
    ) -> None:
        self._documents = documents
        self._lexical = lexical
        self._vectors = vectors
        self._embeddings = embeddings
        self._top_k = top_k
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

        lexical_lists = [self._lexical.search(generation_id, q, self._top_k) for q in lexical_queries]
        semantic_lists: list[list[tuple[UUID, float]]] = []
        for query in semantic_queries:
            vector = next(iter(self._embeddings.embed([query])))
            semantic_lists.append(list(self._vectors.search(generation_id, vector, self._top_k)))

        ranked = reciprocal_rank_fusion(
            {
                "bm25": merge_within_channel(lexical_lists),
                "vector": merge_within_channel(semantic_lists),
            },
            k=self._rrf_k,
        )
        return SearchOutcome(mandatory=mandatory, ranked=ranked)
