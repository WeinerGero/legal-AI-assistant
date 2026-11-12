"""Первый вызов DeepSeek: анализ вопроса и поисковый запрос (ARCHITECTURE.md §6).

Модуль собирает вход ``AnalysisRequestV1`` из сохранённого контекста, вызывает
модель с дословной инструкцией §6.3, проверяет ответ правилами §6.4 и сверяет
реквизиты с корпусом: ``verified_in_corpus`` выставляет сервер, а не модель.

Зависимости — только порты ``core.contracts``; БД, индексы и клиенты
моделей находятся за ними.
"""

from __future__ import annotations

from core.analysis.request import (
    ANALYSIS_PROMPT_ID,
    analyze,
    build_request,
    finalize,
)
from core.analysis.validation import (
    MAX_RECENT_MESSAGES,
    MAX_SEARCH_QUERY_CHARS,
    apply_checks,
    clean_queries,
    contract_number_key,
    contradicts_context,
    is_empty_value,
    normalize_entities,
    trim_history,
)

__all__ = [
    "ANALYSIS_PROMPT_ID",
    "MAX_RECENT_MESSAGES",
    "MAX_SEARCH_QUERY_CHARS",
    "analyze",
    "apply_checks",
    "build_request",
    "clean_queries",
    "contract_number_key",
    "contradicts_context",
    "finalize",
    "is_empty_value",
    "normalize_entities",
    "trim_history",
]
