from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.contracts import AnalysisRequestV1, RenderedAnswerV1


class CoreUnavailable(RuntimeError):
    pass


CoreHandler = Callable[[str], RenderedAnswerV1 | dict[str, Any] | str]


def answer_question(
    request: AnalysisRequestV1, handler: CoreHandler | None
) -> RenderedAnswerV1:
    if handler is None:
        raise CoreUnavailable("The core answer pipeline is not configured.")
    payload = request.model_dump_json()
    result = handler(payload)
    if isinstance(result, str):
        return RenderedAnswerV1.model_validate_json(result)
    return RenderedAnswerV1.model_validate(result)
