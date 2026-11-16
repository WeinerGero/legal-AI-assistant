from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.schemas import AnswerResponse, CoreResult, ModelQuestion


class CoreUnavailable(RuntimeError):
    pass


class InvalidCoreOutput(ValueError):
    pass


CoreHandler = Callable[[str], CoreResult | AnswerResponse | dict[str, Any] | str]


def answer_question(
    request: ModelQuestion, handler: CoreHandler | None
) -> CoreResult:
    if handler is None:
        raise CoreUnavailable("The core answer pipeline is not configured.")
    payload = request.model_dump_json()
    result = handler(payload)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise InvalidCoreOutput("Core returned invalid JSON.") from exc
    if isinstance(result, AnswerResponse):
        result = {"answer": result}
    elif isinstance(result, dict) and "answer" not in result:
        result = {"answer": result}
    try:
        return CoreResult.model_validate(result)
    except ValidationError as exc:
        raise InvalidCoreOutput("Core result or source locations are invalid.") from exc
