from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActiveDocument(StrictModel):
    contract_number: str | None = None
    counterparty: str | None = None


class QuestionContext(StrictModel):
    active_document: ActiveDocument | None = None
    event_date: date | None = None


class RecentMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str


class ModelQuestion(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str
    question: str
    context: QuestionContext
    recent_messages: list[RecentMessage]
    summary: str | None
    corpus_id: str
    index_generation_id: str
    context_revision: int = Field(ge=0)


class Citation(StrictModel):
    answer_source_id: str = Field(min_length=1, max_length=128)
    number: int = Field(ge=1)


class AnswerSegment(StrictModel):
    claim_id: str
    text: str
    citations: list[Citation]


class AnswerBlock(StrictModel):
    block_type: Literal[
        "conclusion", "justification", "qualification", "clarification", "refusal"
    ]
    segments: list[AnswerSegment]


class AnswerSource(StrictModel):
    answer_source_id: str = Field(min_length=1, max_length=128)
    number: int = Field(ge=1)
    document_title: str
    version_label: str
    section_number: str | None


class RetrievalAudit(StrictModel):
    performed: bool
    passes: list[str]


class AnswerResponse(StrictModel):
    schema_version: Literal["1.0"]
    answer_id: str
    legal_status: Literal[
        "answered",
        "partial",
        "clarification_required",
        "insufficient_evidence",
        "out_of_scope",
    ]
    verification_status: Literal["passed", "partial", "failed", "technical_error"]
    missing_information: list[str]
    false_premises: list[str]
    clarification_question: str | None
    blocks: list[AnswerBlock]
    sources: list[AnswerSource]
    retrieval_audit: RetrievalAudit


class SourceLocation(StrictModel):
    answer_source_id: str = Field(min_length=1, max_length=128)
    document_id: UUID
    document_version_id: UUID
    clause_id: UUID
    canonical_text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> SourceLocation:
        if self.end_char <= self.start_char or self.end_char > len(self.canonical_text):
            raise ValueError("Source span must select text inside the canonical document.")
        return self


class CoreResult(StrictModel):
    answer: AnswerResponse
    source_locations: list[SourceLocation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sources(self) -> CoreResult:
        source_ids = [source.answer_source_id for source in self.answer.sources]
        location_ids = [location.answer_source_id for location in self.source_locations]
        citation_ids = {
            citation.answer_source_id
            for block in self.answer.blocks
            for segment in block.segments
            for citation in segment.citations
        }
        if len(location_ids) != len(set(location_ids)) or set(source_ids) != set(location_ids):
            raise ValueError("Each answer source must have exactly one source location.")
        if not citation_ids.issubset(source_ids):
            raise ValueError("Every citation must reference a source in the answer.")
        return self


class SourceSnippet(StrictModel):
    answer_source_id: str = Field(min_length=1, max_length=128)
    number: int
    document_title: str
    version_label: str
    section_number: str | None
    before: str
    highlight: str
    after: str
