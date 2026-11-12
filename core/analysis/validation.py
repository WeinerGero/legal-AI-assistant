"""Программные проверки результата DeepSeek №1 (ARCHITECTURE.md §6.4).

Проверки не исправляют модель молча, а приводят результат к состоянию,
которое можно безопасно передать дальше: допустимые перечисления заданы
типами ``core.contracts``, здесь контролируются корректность дат, пустые
значения, длина поисковых формулировок и согласованность реквизитов.

Модуль работает только с ``core.contracts``: без БД, индексов и моделей.
"""

from __future__ import annotations

from typing import Final

from core.contracts import (
    AnalysisResultV1,
    AnalysisStatus,
    ChatMessage,
    DialogContext,
    EntityValue,
)

# Формулировка длиннее лимита размывает BM25 и векторный канал.
MAX_SEARCH_QUERY_CHARS: Final[int] = 400

# Больше шести сообщений история начинает противоречить контексту (§6.1).
MAX_RECENT_MESSAGES: Final[int] = 6

# Значения, которыми модель обозначает отсутствие реквизита.
EMPTY_MARKERS: Final[frozenset[str]] = frozenset(
    {"", "-", "—", "–", "null", "none", "n/a", "не указан", "не указано", "неизвестно"}
)

# Начала фраз, которыми модель описывает отсутствие значения.
EMPTY_PREFIXES: Final[tuple[str, ...]] = ("не указан", "не известн", "неизвестн")

ENTITY_KEYS: Final[tuple[str, ...]] = ("contract_number", "counterparty", "event_date")


def is_empty_value(value: str | None) -> bool:
    """Пустая строка и заглушки модели не считаются реквизитом."""
    if value is None:
        return True
    normalized = " ".join(value.split()).lower()
    if normalized in EMPTY_MARKERS:
        return True
    return normalized.startswith(EMPTY_PREFIXES)


def contract_number_key(value: str) -> str:
    """Номер договора сравнивается без регистра, пробелов и вида тире."""
    normalized = value.lower()
    for dash in ("—", "–", "−", "―"):
        normalized = normalized.replace(dash, "-")
    return "".join(ch for ch in normalized if ch.isalnum())


def clean_queries(values: list[str]) -> list[str]:
    """Отбрасывает пустые и слишком длинные формулировки, сохраняя порядок."""
    cleaned: list[str] = []
    for value in values:
        text = " ".join(value.split())
        if not text or len(text) > MAX_SEARCH_QUERY_CHARS:
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def clean_values(values: list[str]) -> list[str]:
    """Убирает пустые элементы списков фактов и неоднозначностей."""
    cleaned: list[str] = []
    for value in values:
        text = " ".join(value.split())
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def normalize_entities(
    entities: dict[str, EntityValue], dialog_context: DialogContext
) -> dict[str, EntityValue]:
    """Пустые значения переводятся в ``unknown``, известные реквизиты заполняются.

    Модель вправе вернуть ``"не указан"`` вместо ``null``. Если контекст диалога
    знает реквизит, он берётся из контекста с честным происхождением
    ``dialog_context``, а не выдумывается. Признак ``verified_in_corpus``
    модель выставлять не вправе — серверная сверка выполняется отдельно.
    """
    active_document = dialog_context.active_document or {}
    normalized: dict[str, EntityValue] = {}

    for key in ENTITY_KEYS:
        entity = entities.get(key)
        if entity is not None and not is_empty_value(entity.value):
            assert entity.value is not None
            normalized[key] = entity.model_copy(update={"value": " ".join(entity.value.split())})
            continue

        fallback = active_document.get(key)
        if key in active_document and not is_empty_value(fallback):
            normalized[key] = EntityValue(value=fallback, origin="dialog_context")
        elif entity is not None:
            normalized[key] = EntityValue(value=None, origin="unknown", verification="unknown")

    for key, entity in entities.items():
        if key not in normalized:
            normalized[key] = entity
    return normalized


def contradicts_context(dialog_context: DialogContext, result: AnalysisResultV1) -> bool:
    """Явное противоречие между репликой и уже известным контекстом.

    Новый названный договор законно заменяет прежний (§6.1), поэтому сам
    по себе он не ошибка. Ошибка — молчаливое наследование прежнего номера,
    когда модель не оставила ни неоднозначности, ни уточняющего вопроса.
    """
    active_document = dialog_context.active_document or {}
    known_number = active_document.get("contract_number")
    if is_empty_value(known_number):
        return False

    named = result.entities.get("contract_number")
    if named is None or is_empty_value(named.value):
        return False
    assert known_number is not None
    assert named.value is not None

    if contract_number_key(known_number) == contract_number_key(named.value):
        return False
    if named.origin != "dialog_context":
        return False
    return not result.requires_clarification and not result.ambiguities


def trim_history(messages: list[ChatMessage], limit: int = MAX_RECENT_MESSAGES) -> list[ChatMessage]:
    """Оставляет последние ``limit`` сообщений, порядок сохраняется."""
    if limit <= 0:
        return []
    return messages[-limit:]


def apply_checks(
    result: AnalysisResultV1,
    dialog_context: DialogContext,
    current_message: str = "",
) -> AnalysisResultV1:
    """Возвращает проверенную копию результата анализа."""
    standalone_question = " ".join(result.standalone_question.split())
    lexical = clean_queries(result.search_queries.lexical)
    semantic = clean_queries(result.search_queries.semantic)

    if not standalone_question:
        standalone_question = " ".join(current_message.split())
    if not lexical:
        lexical = clean_queries([standalone_question])
    if not semantic:
        semantic = clean_queries([standalone_question])

    patched = result.model_copy(
        update={
            "standalone_question": standalone_question,
            "entities": normalize_entities(result.entities, dialog_context),
            "requested_facts": clean_values(result.requested_facts),
            "search_queries": result.search_queries.model_copy(
                update={"lexical": lexical, "semantic": semantic}
            ),
            "ambiguities": clean_values(result.ambiguities),
        }
    )

    status: AnalysisStatus = patched.status
    requires_clarification = patched.requires_clarification

    if contradicts_context(dialog_context, patched):
        requires_clarification = True

    if requires_clarification and status == "ready":
        status = "needs_clarification"
    if not standalone_question and not current_message and status == "ready":
        status = "needs_clarification"

    return patched.model_copy(
        update={"status": status, "requires_clarification": requires_clarification}
    )
