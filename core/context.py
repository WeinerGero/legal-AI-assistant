"""Менеджер диалогового контекста (ARCHITECTURE.md §6.1).

Отвечает за:

- сбор структурированного контекста и последних сообщений;
- создание кратких содержаний при накоплении 12 новых сообщений
  сверх уже обобщённого диапазона;
- различение происхождения сведений (история не является доказательством).
"""

from __future__ import annotations

from dataclasses import dataclass

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
    """Применяет ``context_patch`` из ``AnalysisResultV1`` к текущему контексту."""
    if not patch:
        return base
    merged = base.model_copy(deep=True)
    topic = patch.get("topic")
    if topic:
        merged.topic = topic
    return merged
