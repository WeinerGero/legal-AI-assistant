from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.schemas import AnswerResponse, ModelQuestion


class CoreUnavailable(RuntimeError):
    pass


CoreHandler = Callable[[str], AnswerResponse | dict[str, Any] | str]


def answer_question(
    request: ModelQuestion, handler: CoreHandler | None
) -> AnswerResponse:
    if handler is None:
        raise CoreUnavailable("The core answer pipeline is not configured.")
    payload = request.model_dump_json()
    result = handler(payload)
    if isinstance(result, str):
        return AnswerResponse.model_validate_json(result)
    return AnswerResponse.model_validate(result)
