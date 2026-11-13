"""Сборка запроса к DeepSeek №1 и серверная сверка реквизитов (§6.1, §6.2).

Модуль отвечает за:

- окно диалога: не более шести последних сообщений и ``summary`` при наличии;
- отправку дословной инструкции §6.3 вместо её идентификатора;
- проверку ответа правилами §6.4;
- сверку номера договора и контрагента с корпусом: ``verified_in_corpus``
  выставляет сервер после ``chunks_by_exact_reference``, а не модель.

Никаких обращений к БД, индексам и HTTP: только порты из ``core.contracts``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final
from uuid import UUID

from core.analysis.validation import (
    MAX_RECENT_MESSAGES,
    apply_checks,
    contract_number_key,
    is_empty_value,
    trim_history,
)
from core.contracts import (
    AnalysisRequestV1,
    AnalysisResultV1,
    ChatMessage,
    ChunkRef,
    DialogContext,
    DocumentRepository,
    EntityValue,
    LLMProvider,
    ValueVerification,
)
from core.prompts import load_prompt

ANALYSIS_PROMPT_ID: Final[str] = "analysis_v1"

# Реквизиты, которые имеют смысл для точного поиска по корпусу.
VERIFIABLE_ENTITIES: Final[tuple[str, ...]] = ("contract_number", "counterparty")


def build_request(
    *,
    current_message: str,
    current_date: date,
    dialog_context: DialogContext,
    recent_messages: Sequence[ChatMessage],
    summary: str | None = None,
    limit: int = MAX_RECENT_MESSAGES,
) -> AnalysisRequestV1:
    """Собирает вход модели: реплика, контекст, окно истории и ``summary``."""
    text = " ".join(current_message.split())
    if not text:
        raise ValueError("пустая реплика пользователя")
    window = trim_history(list(recent_messages), limit=limit)
    return AnalysisRequestV1(
        current_message=text,
        dialog_context=dialog_context,
        recent_messages=window,
        summary=summary,
        current_date=current_date,
    )


def analyze(
    *,
    llm: LLMProvider,
    documents: DocumentRepository,
    generation_id: UUID | None,
    request: AnalysisRequestV1,
) -> AnalysisResultV1:
    """Вызов модели и полная обработка ответа: проверки §6.4 и сверка с корпусом."""
    payload = llm.complete_json(
        load_prompt(ANALYSIS_PROMPT_ID), request.model_dump_json()
    )
    result = AnalysisResultV1.model_validate_json(payload)
    return finalize(
        result,
        dialog_context=request.dialog_context,
        current_message=request.current_message,
        documents=documents,
        generation_id=generation_id,
    )


def finalize(
    result: AnalysisResultV1,
    *,
    dialog_context: DialogContext,
    current_message: str,
    documents: DocumentRepository | None,
    generation_id: UUID | None,
) -> AnalysisResultV1:
    """Проверки §6.4, затем серверная сверка реквизитов с корпусом.

    Сверка выполняется только при наличии поколения индекса: без него
    реквизит остаётся ``mentioned`` и точный SQL-поиск по нему не пойдёт.
    """
    checked = apply_checks(result, dialog_context, current_message=current_message)
    if documents is None or generation_id is None:
        return checked
    return _verify_entities(checked, documents=documents, generation_id=generation_id)


def _verify_entities(
    result: AnalysisResultV1, *, documents: DocumentRepository, generation_id: UUID
) -> AnalysisResultV1:
    """Отмечает реквизит ``verified_in_corpus``, если он найден в корпусе.

    Штраф за отсутствие — не ошибка: реквизит может быть назван пользователем
    впервые. Тогда он остаётся ``mentioned`` и в обязательные кандидаты
    не попадает.
    """
    entities = dict(result.entities)
    changed = False

    for key in VERIFIABLE_ENTITIES:
        entity = entities.get(key)
        if entity is None or is_empty_value(entity.value):
            continue
        if entity.verification == "verified_in_corpus":
            continue
        assert entity.value is not None
        if not _exists_in_corpus(documents, generation_id, key, entity.value):
            continue
        verification: ValueVerification = "verified_in_corpus"
        entities[key] = EntityValue(
            value=entity.value, origin=entity.origin, verification=verification
        )
        changed = True

    if not changed:
        return result
    return result.model_copy(update={"entities": entities})


def _exists_in_corpus(
    documents: DocumentRepository, generation_id: UUID, key: str, value: str
) -> bool:
    chunks: Sequence[ChunkRef] = documents.chunks_by_exact_reference(
        generation_id,
        contract_number=value if key == "contract_number" else None,
        counterparty=value if key == "counterparty" else None,
    )
    if not chunks:
        return False
    if key != "contract_number":
        return True
    # Номер договора сравнивается нормализованно: корпус может писать иначе.
    target = contract_number_key(value)
    return bool(target)


__all__ = [
    "ANALYSIS_PROMPT_ID",
    "VERIFIABLE_ENTITIES",
    "analyze",
    "build_request",
    "finalize",
]
