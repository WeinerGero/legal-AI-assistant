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
        raise NotImplementedError("реализация в core/retrieval/evidence.py")

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
