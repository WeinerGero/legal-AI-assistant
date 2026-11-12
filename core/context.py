"""Менеджер диалогового контекста (ARCHITECTURE.md §6.1).

Отвечает за:

- сбор структурированного контекста и последних сообщений;
- создание кратких содержаний при накоплении 12 новых сообщений
  сверх уже обобщённого диапазона;
- различение происхождения сведений (история не является доказательством).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.contracts import ChatMessage, DialogContext

SUMMARY_THRESHOLD = 12


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """Подготовленный контекст для DeepSeek №1."""

    dialog_context: DialogContext
    recent_messages: tuple[ChatMessage, ...]
    summary: str | None
    should_summarize: bool


def needs_summary(messages_since_summary: int) -> bool:
    """Краткое содержание создаётся порциями, а не на каждом вопросе."""
    return messages_since_summary >= SUMMARY_THRESHOLD


def merge_context(base: DialogContext, patch: dict[str, str] | None) -> DialogContext:
    """Применяет ``context_patch`` из ``AnalysisResultV1`` к текущему контексту.

    Патч приходит как строковый словарь, поэтому дата события приводится к
    ``date``, а ``active_document`` — к ``dict[str, str | None]``. Новый явно
    названный документ в последнем сообщении важнее сохранённого ранее (§6.1).
    """
    if not patch:
        return base
    merged = base.model_copy(deep=True)
    topic = patch.get("topic")
    if topic:
        merged.topic = topic
    event_date = _coerce_date(patch.get("event_date"))
    if event_date is not None:
        merged.event_date = event_date
    document_kind = patch.get("active_document_kind")
    document_number = patch.get("active_document_number")
    document_date = _coerce_date(patch.get("active_document_date"))
    if document_kind or document_number or document_date:
        merged.active_document = {
            "kind": document_kind or _current(merged, "kind"),
            "number": document_number or _current(merged, "number"),
            "date": document_date.isoformat() if document_date else _current(merged, "date"),
        }
    return merged


def _current(context: DialogContext, key: str) -> str | None:
    document = context.active_document or {}
    return document.get(key)


def _coerce_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
