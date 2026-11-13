"""Сквозной конвейер одного вопроса (ARCHITECTURE.md §1.2, §19).

Порядок этапов:

1. Анализ вопроса (DeepSeek №1) → ``AnalysisResultV1``.
2. Точный поиск по реквизитам + BM25 + векторный поиск → слияние RRF.
3. Переранжирование Qwen3 → реестр доказательств ``E###``.
4. Ответ (DeepSeek №2) → ``GenerationResultV1``.
5. Программная проверка → смысловая проверка (DeepSeek №3).
6. Не более одной попытки исправления с повторной проверкой.
7. Преобразование ``evidence_id`` в постоянные ``answer_source_id``.

Модуль зависит только от порт-интерфейсов из ``core.contracts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from core.contracts import (
    AnalysisRequestV1,
    AnalysisResultV1,
    AnswerRepository,
    ChatMessage,
    CitationResolver,
    ContextRepository,
    DialogContext,
    DocumentRepository,
    EmbeddingProvider,
    EntityValue,
    Evidence,
    GenerationRequestV1,
    GenerationResultV1,
    LexicalSearch,
    LLMProvider,
    PipelineRunMetadata,
    RenderedAnswerV1,
    Reranker,
    TechnicalFailure,
    TemporalContext,
    VerificationRequestV1,
    VerificationResultV1,
    VectorSearch,
)
from core.retrieval.evidence import build_evidence
from core.retrieval.rerank import rerank_candidates
from core.retrieval.search import HybridRetriever


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Настройки проекта, а не требования организаторов (ARCHITECTURE.md §7.4)."""

    top_k_lexical: int = 30
    top_k_vector: int = 30
    rrf_k: int = 60
    rerank_candidates: int = 30
    reranker_top_n: int = 10
    recent_messages: int = 6
    allow_one_repair: bool = True


@dataclass(frozen=True, slots=True)
class PipelineAdapters:
    """Набор реализаций портов для конкретного режима (веб или конкурс)."""

    documents: DocumentRepository
    contexts: ContextRepository
    lexical: LexicalSearch
    vectors: VectorSearch
    embeddings: EmbeddingProvider
    reranker: Reranker
    llm: LLMProvider
    answers: AnswerRepository
    citations: CitationResolver


@dataclass(slots=True)
class PipelineOutcome:
    """Результат запуска: либо проверенный ответ, либо технический отказ."""

    answer: RenderedAnswerV1 | None = None
    failure: TechnicalFailure | None = None
    metadata: PipelineRunMetadata | None = None
    analysis: AnalysisResultV1 | None = None
    generation: GenerationResultV1 | None = None
    verification: VerificationResultV1 | None = None
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.answer is not None and self.failure is None


class LegalPipeline:
    """Оркестратор этапов. Не содержит логики доступа к БД и UI."""

    def __init__(self, adapters: PipelineAdapters, config: PipelineConfig | None = None) -> None:
        self._adapters = adapters
        self._config = config or PipelineConfig()

    # --- Этап 1: анализ -----------------------------------------------

    def analyze(
        self,
        *,
        conversation_id: UUID,
        current_message: str,
        current_date: date,
    ) -> AnalysisResultV1:
        context: DialogContext = self._adapters.contexts.load_context(conversation_id)
        recent: tuple[ChatMessage, ...] = tuple(
            self._adapters.contexts.load_recent_messages(
                conversation_id, self._config.recent_messages
            )
        )
        request = AnalysisRequestV1(
            current_message=current_message,
            dialog_context=context,
            recent_messages=list(recent),
            current_date=current_date,
        )
        payload = self._adapters.llm.complete_json("analysis_v1", request.model_dump_json())
        return AnalysisResultV1.model_validate_json(payload)

    # --- Этапы 2–3: поиск и доказательства -----------------------------

    def collect_evidence(
        self, *, generation_id: UUID, analysis: AnalysisResultV1
    ) -> list[Evidence]:
        """Точные кандидаты → BM25 + вектор → RRF → переранжирование → реестр ``E###``."""
        search = HybridRetriever(
            self._adapters.documents,
            self._adapters.lexical,
            self._adapters.vectors,
            self._adapters.embeddings,
            top_k=self._config.top_k_lexical,
            rrf_k=self._config.rrf_k,
        )
        contract_number, counterparty = _exact_references(analysis)
        outcome = search.search(
            generation_id=generation_id,
            lexical_queries=analysis.search_queries.lexical,
            semantic_queries=analysis.search_queries.semantic,
            contract_number=contract_number,
            counterparty=counterparty,
        )

        candidates = [
            (chunk_id, self._adapters.documents.get_chunk(chunk_id))
            for chunk_id, _score in outcome.ranked[: self._config.rerank_candidates]
        ]
        ranked = rerank_candidates(
            self._adapters.reranker,
            query=analysis.standalone_question,
            candidates=[(chunk_id, chunk.search_text) for chunk_id, chunk in candidates],
            top_n=self._config.reranker_top_n,
        )

        selection = build_evidence(
            documents=self._adapters.documents,
            mandatory=outcome.mandatory,
            ranked=[chunk_id for chunk_id, _score in ranked],
            requested_facts=analysis.requested_facts,
        )
        return selection.evidence

    # --- Этап 4: генерация --------------------------------------------

    def generate(self, request: GenerationRequestV1) -> GenerationResultV1:
        payload = self._adapters.llm.complete_json("generation_v1", request.model_dump_json())
        return GenerationResultV1.model_validate_json(payload)

    # --- Этап 5: проверка ---------------------------------------------

    def verify(self, request: VerificationRequestV1) -> VerificationResultV1:
        payload = self._adapters.llm.complete_json("verification_v1", request.model_dump_json())
        return VerificationResultV1.model_validate_json(payload)

    # --- Этапы 6–7: исправление и цитаты -------------------------------

    def repair(self, request: VerificationRequestV1) -> GenerationResultV1:
        payload = self._adapters.llm.complete_json("repair_v1", request.model_dump_json())
        return GenerationResultV1.model_validate_json(payload)

    def run(
        self,
        *,
        conversation_id: UUID,
        current_message: str,
        current_date: date,
        corpus_id: UUID,
    ) -> PipelineOutcome:
        """Сквозной запуск. Технические ошибки не превращаются в юридический отказ."""
        raise NotImplementedError("оркестрация этапов и повторная проверка")

    async def run_async(
        self,
        *,
        conversation_id: UUID,
        current_message: str,
        current_date: date,
        corpus_id: UUID,
    ) -> PipelineOutcome:
        """Асинхронная обёртка, используемая worker-процессом."""
        raise NotImplementedError("асинхронный запуск поверх run()")


def _verified(entity: EntityValue | None) -> str | None:
    """Реквизит годится для точного SQL-поиска, только если он подтверждён.

    Непроверенный или унаследованный от контекста реквизит может относиться
    к другому договору, поэтому в обязательные кандидаты он не попадает.
    """
    if (
        entity is None
        or entity.verification != "verified_in_corpus"
        or not entity.value
    ):
        return None
    return entity.value


def _exact_references(analysis: AnalysisResultV1) -> tuple[str | None, str | None]:
    """Извлекает реквизиты договора и контрагента для точного поиска (§7.3)."""
    return (
        _verified(analysis.entities.get("contract_number")),
        _verified(analysis.entities.get("counterparty")),
    )
