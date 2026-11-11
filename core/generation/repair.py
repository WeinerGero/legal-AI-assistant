"""Маршрутизация исправления после проверки (ARCHITECTURE.md §9.5).

Допускается один дополнительный вызов DeepSeek с последующей полной
повторной проверкой. Бесконечный цикл исправления запрещён.
"""

from __future__ import annotations

from typing import Literal

from core.contracts import VerificationResultV1

RepairDecision = Literal["publish", "repair", "retrieve_more", "clarify", "fail"]


def decide_repair(result: VerificationResultV1) -> RepairDecision:
    """Определяет следующий шаг по вердикту проверяющей модели."""
    if result.verdict == "pass":
        return "publish"
    if result.requires_additional_search or result.verdict == "retrieve_more":
        return "retrieve_more"
    if result.verdict == "clarification_required":
        return "clarify"
    if result.verdict == "revision_required":
        actions = {check.required_action for check in result.claim_checks}
        if actions & {"remove_claim", "revise_claim"}:
            return "repair"
        if "retrieve_more" in actions:
            return "retrieve_more"
        if "ask_clarification" in actions:
            return "clarify"
        return "repair"
    return "fail"


def publishable_claims(result: VerificationResultV1) -> list[str]:
    """Утверждения, которые можно опубликовать после двух неудачных проверок.

    Только независимо подтверждённые, без ``unsupported``/``contradicted``.
    """
    return [
        check.claim_id
        for check in result.claim_checks
        if check.status == "supported" and check.required_action == "none"
    ]


def only_supported_part(result: VerificationResultV1) -> bool:
    """Признак безопасного завершения: все проверки прошли как ``supported``."""
    return all(check.status == "supported" for check in result.claim_checks)
