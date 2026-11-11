"""Проверяемые межэтапные контракты предметного ядра (ARCHITECTURE.md §1.3, §19).

Модуль обязан оставаться чистым: только стандартная библиотека и Pydantic.
Запрещены импорты FastAPI, Streamlit, SQLAlchemy, FAISS и клиентов БД.

Контракты делятся на две группы:

1. Порт-интерфейсы адаптеров хранения и внешних моделей
   (``DocumentRepository`` … ``CitationResolver``).
2. Схемы полезной нагрузки конвейера одного вопроса
   (``AnalysisResultV1``, ``GenerationResultV1``, ``VerificationResultV1`` …).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol, Sequence, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

# --- Перечисления, совпадающие с разделом 19.2 -----------------------------


Intent = Literal[
    "contract_terms",
    "law_lookup",
    "conversation_meta",
    "out_of_scope",
]
AnalysisStatus = Literal["ready", "needs_clarification", "out_of_scope"]
ValueOrigin = Literal["current_message", "dialog_context", "history", "inferred", "unknown"]
ValueVerification = Literal["mentioned", "resolved", "verified_in_corpus", "unknown"]
ClaimType = Literal["legal_fact", "legal_inference", "qualification", "non_factual"]
BlockType = Literal["conclusion", "justification", "qualification", "clarification", "refusal"]
LegalStatus = Literal[
    "answered",
    "partial",
    "clarification_required",
    "insufficient_evidence",
    "out_of_scope",
]
VerificationState = Literal["supported", "partially_supported", "unsupported", "contradicted"]
RequiredAction = Literal[
    "none",
    "revise_claim",
    "remove_claim",
    "retrieve_more",
    "ask_clarification",
]
Verdict = Literal[
    "pass",
    "revision_required",
    "retrieve_more",
    "clarification_required",
    "insufficient_evidence",
]
TechnicalErrorCode = Literal["model_unavailable", "timeout", "invalid_model_output", "internal_error"]
RelationType = Literal["amends", "supersedes", "clarifies", "exception_to", "annex_to"]
JobType = Literal["answer", "index"]
JobStatus = Literal["pending", "running", "completed", "failed"]
IndexGenerationStatus = Literal["building", "validating", "ready", "retired", "failed"]
UserRole = Literal["user", "admin"]


# --- Схемы предметной области ----------------------------------------------


class EntityValue(BaseModel):
    """Значение реквизита с явным происхождением и уровнем проверки."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    origin: ValueOrigin = "unknown"
    verification: ValueVerification = "unknown"


class DialogContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_document: dict[str, str | None] | None = None
    event_date: date | None = None
    topic: str | None = None
    unresolved_ambiguities: list[str] = Field(default_factory=list)
    last_context_message_id: UUID | None = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class AnalysisRequestV1(BaseModel):
    """Вход DeepSeek №1 (ARCHITECTURE.md §6.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    current_message: str
    dialog_context: DialogContext = Field(default_factory=DialogContext)
    recent_messages: list[ChatMessage] = Field(default_factory=list)
    summary: str | None = None
    current_date: date


class SearchQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lexical: list[str] = Field(default_factory=list)
    semantic: list[str] = Field(default_factory=list)


class AnalysisResultV1(BaseModel):
    """Выход DeepSeek №1 (ARCHITECTURE.md §6.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    status: AnalysisStatus = "ready"
    intent: Intent = "contract_terms"
    standalone_question: str
    entities: dict[str, EntityValue] = Field(default_factory=dict)
    requested_facts: list[str] = Field(default_factory=list)
    search_queries: SearchQueries = Field(default_factory=SearchQueries)
    ambiguities: list[str] = Field(default_factory=list)
    requires_clarification: bool = False
    context_patch: dict[str, str] | None = None


class Evidence(BaseModel):
    """Запись реестра доказательств, доступная модели как ``evidence_id``."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^E\d{3,}$")
    chunk_id: UUID
    document_version_id: UUID
    clause_id: UUID
    document_title: str
    section: str
    text: str
    applicability_note: str | None = None


class ApplicabilityPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Literal["eq", "gte", "lte", "lt", "gt"]
    value: str | int | float


class ApplicabilityCondition(BaseModel):
    """Типизированное условие применимости (ARCHITECTURE.md §3.4, §7.1).

    Строковая формулировка условия хранится отдельно как часть доказательства
    и никогда не исполняется как код.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: list[ApplicabilityPredicate] | None = None
    any: list[ApplicabilityPredicate] | None = None
    not_: ApplicabilityPredicate | None = Field(default=None, alias="not")



class TemporalContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_date: date | None = None
    applicability_unknown: bool = False


class GenerationRequestV1(BaseModel):
    """Вход DeepSeek №2 (ARCHITECTURE.md §8.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    question: str
    requested_facts: list[str] = Field(default_factory=list)
    temporal_context: TemporalContext = Field(default_factory=TemporalContext)
    evidence: list[Evidence] = Field(default_factory=list)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^C\d{3,}$")
    claim_type: ClaimType
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(pattern=r"^B\d{3,}$")
    block_type: BlockType
    segments: list[Segment] = Field(default_factory=list)


class GenerationResultV1(BaseModel):
    """Выход DeepSeek №2 (ARCHITECTURE.md §8.3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    legal_status: LegalStatus = "answered"
    blocks: list[Block] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ClaimCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: VerificationState
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    required_action: RequiredAction = "none"


class VerificationRequestV1(BaseModel):
    """Вход DeepSeek №3 (ARCHITECTURE.md §9.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    question: str
    requested_facts: list[str] = Field(default_factory=list)
    generation: GenerationResultV1
    evidence: list[Evidence] = Field(default_factory=list)


class VerificationResultV1(BaseModel):
    """Выход DeepSeek №3 (ARCHITECTURE.md §9.3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    verdict: Verdict
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    missing_requested_facts: list[str] = Field(default_factory=list)
    requires_additional_search: bool = False


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_source_id: str
    number: int


class RenderedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    citations: list[Citation] = Field(default_factory=list)


class RenderedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_type: BlockType
    segments: list[RenderedSegment] = Field(default_factory=list)


class RenderedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_source_id: str
    number: int
    document_title: str
    version_label: str
    section_number: str | None = None


class RenderedAnswerV1(BaseModel):
    """Формат ответа для интерфейса (ARCHITECTURE.md §10.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    answer_id: str
    legal_status: LegalStatus
    verification_status: Literal["passed", "partial", "failed", "technical_error"]
    blocks: list[RenderedBlock] = Field(default_factory=list)
    sources: list[RenderedSource] = Field(default_factory=list)


class TechnicalFailure(BaseModel):
    """Технический отказ отличается от юридического ``insufficient_evidence``."""

    model_config = ConfigDict(extra="forbid")

    error_code: TechnicalErrorCode
    message: str
    request_id: str


# --- Порт-интерфейсы адаптеров (ARCHITECTURE.md §1.3) ----------------------


class DocumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: UUID
    document_id: UUID
    document_version_id: UUID
    source_path: str
    version_label: str
    title: str
    canonical_text: str


class ChunkRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: UUID
    document_version_id: UUID
    start_char: int
    end_char: int
    search_text: str
    clause_ids: tuple[UUID, ...] = ()


@runtime_checkable
class DocumentRepository(Protocol):
    """Доступ к корпусу и поколению индекса. Реализуется PostgreSQL и ZIP."""

    def current_generation(self, corpus_id: UUID) -> UUID: ...

    def get_chunk(self, chunk_id: UUID) -> ChunkRef: ...

    def get_document_version(self, version_id: UUID) -> DocumentRef: ...

    def chunks_by_exact_reference(
        self,
        generation_id: UUID,
        contract_number: str | None = None,
        counterparty: str | None = None,
    ) -> Sequence[ChunkRef]: ...


@runtime_checkable
class ContextRepository(Protocol):
    """История диалога. Веб — PostgreSQL, конкурс — одна строка CSV."""

    def load_context(self, conversation_id: UUID) -> DialogContext: ...

    def load_recent_messages(self, conversation_id: UUID, limit: int) -> Sequence[ChatMessage]: ...


@runtime_checkable
class LexicalSearch(Protocol):
    """BM25-канал над снимком поколения индекса."""

    def search(self, generation_id: UUID, query: str, top_k: int) -> Sequence[tuple[UUID, float]]: ...


@runtime_checkable
class VectorSearch(Protocol):
    """Векторный канал: pgvector или FAISS ``IndexFlatIP``."""

    def search(self, generation_id: UUID, embedding: Sequence[float], top_k: int) -> Sequence[tuple[UUID, float]]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """BGE-M3, 1024 измерения."""

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@runtime_checkable
class Reranker(Protocol):
    """Qwen3 Reranker; получает тексты, возвращает индексы входного массива."""

    def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> Sequence[tuple[int, float | None]]: ...


@runtime_checkable
class LLMProvider(Protocol):
    """DeepSeek V4.1 Flash через разрешённый прокси."""

    def complete_json(self, system_prompt: str, payload: str) -> str: ...


@runtime_checkable
class AnswerRepository(Protocol):
    def save_answer(self, rendered: RenderedAnswerV1) -> UUID: ...


@runtime_checkable
class CitationResolver(Protocol):
    """``evidence_id`` → постоянный ``answer_source_id`` (ARCHITECTURE.md §10.1)."""

    def resolve(self, answer_id: UUID, claims: Sequence[tuple[str, Sequence[str]]]) -> RenderedAnswerV1: ...


class PipelineRunMetadata(BaseModel):
    """Воспроизводимость запуска (ARCHITECTURE.md §16.3). Без секретов."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    pipeline_version: str
    corpus_id: UUID
    corpus_manifest_hash: str
    index_generation_id: UUID
    parser_version: str
    chunker_version: str
    embedding_model: str
    reranker_model: str
    generation_model: str
    prompt_versions: dict[str, str]
    started_at: datetime | None = None
