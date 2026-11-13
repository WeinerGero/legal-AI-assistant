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

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError

from core.analysis import analyze, build_request
from core.contracts import (
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
    FactAudit,
    GenerationRequestV1,
    GenerationResultV1,
    LexicalSearch,
    LLMProvider,
    PipelineRunMetadata,
    RenderedAnswerV1,
    Reranker,
    RetrievalAuditV1,
    TechnicalFailure,
    TechnicalErrorCode,
    VerificationRequestV1,
    VerificationResultV1,
    VectorSearch,
)
from core.prompts import load_prompt
from core.generation.answer import build_generation_request
from core.generation.repair import decide_repair
from core.retrieval.evidence import EvidenceSelection, build_evidence, covered_facts
from core.retrieval.rerank import rerank_candidates
from core.retrieval.search import HybridRetriever
from core.verification.programmatic import (
    check_evidence_integrity,
    check_result,
    check_verification_coverage,
)
from core.verification.semantic import build_verification_request, is_pass

GENERATION_PROMPT_ID = "generation_v1"
VERIFICATION_PROMPT_ID = "verification_v1"
REPAIR_PROMPT_ID = "repair_v1"


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
    retrieval_audit: RetrievalAuditV1 | None = None

    @property
    def succeeded(self) -> bool:
        return self.answer is not None and self.failure is None


class LegalPipeline:
    """Оркестратор этапов. Не содержит логики доступа к БД и UI."""

    def __init__(
        self, adapters: PipelineAdapters, config: PipelineConfig | None = None
    ) -> None:
        self._adapters = adapters
        self._config = config or PipelineConfig()

    # --- Этап 1: анализ -----------------------------------------------

    def analyze(
        self,
        *,
        conversation_id: UUID,
        current_message: str,
        current_date: date,
        generation_id: UUID | None = None,
        summary: str | None = None,
    ) -> AnalysisResultV1:
        """Вызов DeepSeek №1 с проверками §6.4 и сверкой реквизитов с корпусом.

        Сверка ``verified_in_corpus`` идёт только при известном поколении
        индекса: без него реквизит остаётся ``mentioned`` и точный SQL-поиск
        по нему не выполняется.
        """
        context: DialogContext = self._adapters.contexts.load_context(conversation_id)
        recent: tuple[ChatMessage, ...] = tuple(
            self._adapters.contexts.load_recent_messages(
                conversation_id, self._config.recent_messages
            )
        )
        request = build_request(
            current_message=current_message,
            current_date=current_date,
            dialog_context=context,
            recent_messages=recent,
            summary=summary,
            limit=self._config.recent_messages,
        )
        return analyze(
            llm=self._adapters.llm,
            documents=self._adapters.documents,
            generation_id=generation_id,
            request=request,
        )

    # --- Этапы 2–3: поиск и доказательства -----------------------------

    def collect_evidence(
        self,
        *,
        generation_id: UUID,
        analysis: AnalysisResultV1,
        broad: bool = False,
    ) -> EvidenceSelection:
        """Точные кандидаты → BM25 + вектор → RRF → переранжирование → реестр ``E###``."""
        multiplier = 2 if broad else 1
        search = HybridRetriever(
            self._adapters.documents,
            self._adapters.lexical,
            self._adapters.vectors,
            self._adapters.embeddings,
            top_k=self._config.top_k_lexical * multiplier,
            top_k_vector=self._config.top_k_vector * multiplier,
            rrf_k=self._config.rrf_k,
        )
        contract_number, counterparty = _exact_references(analysis)
        outcome = search.search(
            generation_id=generation_id,
            lexical_queries=(
                analysis.search_queries.lexical + analysis.requested_facts
                if broad
                else analysis.search_queries.lexical
            ),
            semantic_queries=(
                analysis.search_queries.semantic + analysis.requested_facts
                if broad
                else analysis.search_queries.semantic
            ),
            contract_number=contract_number,
            counterparty=counterparty,
        )

        if broad:
            ranked_ids = [chunk_id for chunk_id, _score in outcome.ranked]
        else:
            candidates = [
                (chunk_id, self._adapters.documents.get_chunk(chunk_id))
                for chunk_id, _score in outcome.ranked[: self._config.rerank_candidates]
            ]
            ranked = rerank_candidates(
                self._adapters.reranker,
                query=analysis.standalone_question,
                candidates=[
                    (chunk_id, chunk.search_text) for chunk_id, chunk in candidates
                ],
                top_n=self._config.reranker_top_n,
            )
            ranked_ids = [chunk_id for chunk_id, _score in ranked]

        return build_evidence(
            documents=self._adapters.documents,
            mandatory=outcome.mandatory,
            ranked=ranked_ids,
            requested_facts=analysis.requested_facts,
        )

    # --- Этап 4: генерация --------------------------------------------

    def generate(self, request: GenerationRequestV1) -> GenerationResultV1:
        """Ответ DeepSeek №2: системный промпт передаётся дословно (§6.3, §8.2)."""
        payload = self._adapters.llm.complete_json(
            load_prompt(GENERATION_PROMPT_ID), request.model_dump_json()
        )
        return GenerationResultV1.model_validate_json(payload)

    # --- Этап 5: проверка ---------------------------------------------

    def verify(self, request: VerificationRequestV1) -> VerificationResultV1:
        """Смысловая проверка DeepSeek №3 по полному ответу и основаниям (§9.2)."""
        payload = self._adapters.llm.complete_json(
            load_prompt(VERIFICATION_PROMPT_ID), request.model_dump_json()
        )
        return VerificationResultV1.model_validate_json(payload)

    # --- Этапы 6–7: исправление и цитаты -------------------------------

    def repair(
        self,
        request: VerificationRequestV1,
        verification: VerificationResultV1,
    ) -> GenerationResultV1:
        """Единственная попытка исправления с последующей повторной проверкой (§9.4)."""
        repair_payload = {
            "question": request.question,
            "requested_facts": request.requested_facts,
            "generation": request.generation.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in request.evidence],
            "verification": verification.model_dump(mode="json"),
        }
        payload = self._adapters.llm.complete_json(
            load_prompt(REPAIR_PROMPT_ID),
            json.dumps(repair_payload, ensure_ascii=False),
        )
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
        request_id = uuid4()
        outcome = PipelineOutcome()
        try:
            generation_id = self._adapters.documents.current_generation(corpus_id)
            analysis = self.analyze(
                conversation_id=conversation_id,
                current_message=current_message,
                current_date=current_date,
                generation_id=generation_id,
            )
            outcome.analysis = analysis

            evidence: list[Evidence] = []
            retrieval_audit: RetrievalAuditV1 | None = None
            if analysis.status == "ready" and analysis.request_purpose not in {
                "prohibited_assistance",
                "nonlegal",
            }:
                selection = self.collect_evidence(
                    generation_id=generation_id, analysis=analysis
                )
                passes: list[Literal["primary_hybrid", "independent_broad"]] = [
                    "primary_hybrid"
                ]
                if selection.missing_facts:
                    selection = self.collect_evidence(
                        generation_id=generation_id,
                        analysis=analysis,
                        broad=True,
                    )
                    passes.append("independent_broad")
                evidence = selection.evidence
                retrieval_audit = _build_retrieval_audit(
                    corpus_id=corpus_id,
                    generation_id=generation_id,
                    requested_facts=analysis.requested_facts,
                    evidence=evidence,
                    missing=selection.missing_facts,
                    passes=passes,
                )
            outcome.evidence = evidence
            outcome.retrieval_audit = retrieval_audit
            registry = {item.evidence_id: item for item in evidence}
            integrity = check_evidence_integrity(registry)
            if not integrity.ok:
                raise ValueError("; ".join(integrity.errors))

            context = self._adapters.contexts.load_context(conversation_id)
            generation_request = build_generation_request(
                analysis,
                evidence,
                event_date=context.event_date,
                applicability_unknown=context.event_date is None,
                retrieval_audit=retrieval_audit,
            )
            generation = self.generate(generation_request)
            outcome.generation = generation
            formal = check_result(generation, registry=registry)
            if not formal.ok:
                raise ValueError("; ".join(formal.errors))

            verification_request = build_verification_request(
                question=analysis.standalone_question,
                requested_facts=analysis.requested_facts,
                generation=generation,
                registry=registry,
            )
            verification = self.verify(verification_request)
            outcome.verification = verification
            coverage = check_verification_coverage(generation, verification)
            passed = coverage.ok and is_pass(verification, generation)

            if not passed and self._config.allow_one_repair:
                decision = decide_repair(verification)
                if decision == "repair":
                    generation = self.repair(verification_request, verification)
                    outcome.generation = generation
                    formal = check_result(generation, registry=registry)
                    if not formal.ok:
                        raise ValueError("; ".join(formal.errors))
                    verification_request = build_verification_request(
                        question=analysis.standalone_question,
                        requested_facts=analysis.requested_facts,
                        generation=generation,
                        registry=registry,
                    )
                    verification = self.verify(verification_request)
                    outcome.verification = verification
                    coverage = check_verification_coverage(generation, verification)
                    passed = coverage.ok and is_pass(verification, generation)

            if not passed:
                raise ValueError("итоговый ответ не прошёл полную проверку утверждений")

            rendered = self._adapters.citations.resolve(
                request_id,
                generation,
                registry,
                "passed",
            )
            rendered = rendered.model_copy(update={"retrieval_audit": retrieval_audit})
            self._adapters.answers.save_answer(rendered)
            outcome.answer = rendered
            return outcome
        except (
            Exception
        ) as exc:  # Граница предметного ядра: наружу уходит типизированный отказ.
            outcome.failure = TechnicalFailure(
                error_code=_technical_error_code(exc),
                message=_technical_error_message(exc),
                request_id=str(request_id),
            )
            return outcome

    async def run_async(
        self,
        *,
        conversation_id: UUID,
        current_message: str,
        current_date: date,
        corpus_id: UUID,
    ) -> PipelineOutcome:
        """Асинхронная обёртка, используемая worker-процессом."""
        import asyncio

        return await asyncio.to_thread(
            self.run,
            conversation_id=conversation_id,
            current_message=current_message,
            current_date=current_date,
            corpus_id=corpus_id,
        )


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


def _technical_error_code(exc: Exception) -> TechnicalErrorCode:
    if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
        return "timeout"
    if isinstance(exc, (ValidationError, ValueError, json.JSONDecodeError)):
        return "invalid_model_output"
    if (
        "unavailable" in str(exc).lower()
        or "429" in str(exc)
        or "5xx" in str(exc).lower()
    ):
        return "model_unavailable"
    return "internal_error"


def _technical_error_message(exc: Exception) -> str:
    code = _technical_error_code(exc)
    messages = {
        "timeout": "Превышено время обработки запроса.",
        "model_unavailable": "Сервис модели временно недоступен.",
        "invalid_model_output": "Получен некорректный структурированный ответ модели.",
        "internal_error": "Не удалось завершить обработку запроса.",
    }
    return messages[code]


def _build_retrieval_audit(
    *,
    corpus_id: UUID,
    generation_id: UUID,
    requested_facts: list[str],
    evidence: list[Evidence],
    missing: list[str],
    passes: list[Literal["primary_hybrid", "independent_broad"]],
) -> RetrievalAuditV1:
    covered = set(covered_facts(evidence, requested_facts))
    versions = sorted({item.document_version_id for item in evidence}, key=str)
    facts = [
        FactAudit(
            fact_id=f"F{position:03d}",
            status="found_applicable" if fact in covered else "not_confirmed",
            evidence_ids=(
                [item.evidence_id for item in evidence] if fact in covered else []
            ),
            searched_document_versions=versions,
        )
        for position, fact in enumerate(requested_facts, start=1)
    ]
    unresolved = [
        f"F{position:03d}"
        for position, fact in enumerate(requested_facts, start=1)
        if fact in missing
    ]
    return RetrievalAuditV1(
        corpus_id=str(corpus_id),
        index_generation_id=generation_id,
        passes=passes,
        requested_facts=facts,
        expanded_search_completed="independent_broad" in passes,
        unresolved_fact_ids=unresolved,
    )
