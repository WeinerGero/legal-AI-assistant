from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


ROUTING_PROMPT = (Path(__file__).resolve().parents[1] / "core" / "prompts" / "routing_v1.txt").read_text(encoding="utf-8")


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Literal["legal", "conversation"]
    answer_style: Literal["brief", "standard", "detailed"]
    action: Literal["reply", "history"] | None
    history_target: Literal["last_user", "first_user", "last_assistant"] | None
    reply: str | None

    @model_validator(mode="after")
    def validate_route(self) -> RouteDecision:
        if self.route == "legal" and (self.action is not None or self.history_target is not None or self.reply is not None):
            raise ValueError("legal route must not contain a conversational answer")
        if self.route == "conversation":
            if self.action == "history" and (self.history_target is None or self.reply is not None):
                raise ValueError("history route requires an exact history target")
            if self.action == "reply" and (self.history_target is not None or not isinstance(self.reply, str) or not self.reply.strip()):
                raise ValueError("conversation route requires a reply")
            if self.action is None:
                raise ValueError("conversation route requires an action")
        return self


def parse_route(raw: str) -> RouteDecision:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return RouteDecision.model_validate(json.loads(text))
