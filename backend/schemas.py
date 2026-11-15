from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActiveDocument(StrictModel):
    contract_number: str | None = None
    counterparty: str | None = None


class QuestionContext(StrictModel):
    active_document: ActiveDocument | None
    event_date: date | None


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
    answer_source_id: str
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
    answer_source_id: str
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
