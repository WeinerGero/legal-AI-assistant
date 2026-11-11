"""Применимость редакций по датам и условиям (ARCHITECTURE.md §3.4, §7.1).

Условие применения возвращает одно из ``true``, ``false``, ``unknown``.
``unknown`` **не** считается выполненным условием.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from core.contracts import ApplicabilityCondition

ComparisonResult = Literal["true", "false", "unknown"]


def _compare(field_value: Any, operator: str, expected: Any) -> ComparisonResult:
    if field_value is None:
        return "unknown"
    try:
        if operator == "eq":
            return "true" if field_value == expected else "false"
        if operator == "gte":
            return "true" if field_value >= expected else "false"
        if operator == "lte":
            return "true" if field_value <= expected else "false"
        if operator == "lt":
            return "true" if field_value < expected else "false"
        if operator == "gt":
            return "true" if field_value > expected else "false"
    except TypeError:
        return "unknown"
    return "unknown"


def evaluate_condition(
    condition: ApplicabilityCondition, facts: dict[str, Any]
) -> ComparisonResult:
    """Вычисляет типизированное условие; неизвестное поле даёт ``unknown``."""
    predicates: list[ComparisonResult] = []
    if condition.all is not None:
        predicates = [_evaluate_predicate(p, facts) for p in condition.all]
        if any(result == "false" for result in predicates):
            return "false"
        if any(result == "unknown" for result in predicates):
            return "unknown"
        return "true"
    if condition.any is not None:
        predicates = [_evaluate_predicate(p, facts) for p in condition.any]
        if any(result == "true" for result in predicates):
            return "true"
        if any(result == "unknown" for result in predicates):
            return "unknown"
        return "false"
    if condition.not_ is not None:
        inner = _evaluate_predicate(condition.not_, facts)
        return {"true": "false", "false": "true", "unknown": "unknown"}[inner]
    return "unknown"


def _evaluate_predicate(predicate: Any, facts: dict[str, Any]) -> ComparisonResult:
    if predicate.field not in facts:
        return "unknown"
    return _compare(facts[predicate.field], predicate.operator, predicate.value)


def is_applicable_on(
    *,
    event_date: date | None,
    effective_from: date | None,
    effective_to: date | None,
) -> ComparisonResult:
    """Проверка попадания даты события в интервал действия редакции."""
    if event_date is None:
        return "unknown"
    if effective_from is not None and event_date < effective_from:
        return "false"
    if effective_to is not None and event_date >= effective_to:
        return "false"
    return "true"
