"""Строгая запись и автономное воспроизведение вызовов внешних моделей."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

APIMode = Literal["live", "record", "replay"]


class ReplayMismatchError(RuntimeError):
    """Текущий запрос или детерминированный checkpoint не совпал с записью."""


class RecordedAPIError(RuntimeError):
    """Воспроизводимая ошибка, полученная при исходном API-вызове."""


class APIRecordingStore:
    def __init__(self, mode: APIMode, root: Path) -> None:
        if mode not in {"live", "record", "replay"}:
            raise ValueError(f"неизвестный API_MODE: {mode}")
        self.mode = mode
        self.root = root
        self.responses_dir = root / "responses"
        self.trace_path = root / "trace.json"
        self.current_question_id: str | None = None
        self.trace: list[dict[str, Any]] = []
        self.replay_position = 0
        if mode == "record":
            self.responses_dir.mkdir(parents=True, exist_ok=True)
        elif mode == "replay":
            if not self.trace_path.is_file() or not self.responses_dir.is_dir():
                raise FileNotFoundError(f"не найдена полная запись API: {root}")
            payload = json.loads(self.trace_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise ReplayMismatchError("некорректный trace.json")
            self.trace = payload["events"]

    @classmethod
    def from_env(cls) -> "APIRecordingStore":
        mode = os.environ.get("API_MODE", "live").strip().lower()
        root = Path(os.environ.get("API_RECORDINGS_DIR", "artifacts/api-recordings"))
        return cls(mode, root)  # type: ignore[arg-type]

    def set_question(self, question_id: str | None) -> None:
        self.current_question_id = question_id

    def request_key(
        self,
        *,
        service: str,
        stage: str,
        endpoint: str,
        payload: dict,
    ) -> str:
        return _sha256(
            {
                "service": service,
                "stage": stage,
                "endpoint": endpoint,
                "payload": payload,
            }
        )

    def load(self, key: str) -> dict:
        path = self.responses_dir / f"{key}.json"
        if not path.is_file():
            raise ReplayMismatchError(f"нет записи для запроса {key}")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or envelope.get("request_sha256") != key:
            raise ReplayMismatchError(f"повреждена запись запроса {key}")
        if envelope.get("ok") is False:
            raise RecordedAPIError(str(envelope.get("error_type") or "APIError"))
        response = envelope.get("response")
        if not isinstance(response, dict):
            raise ReplayMismatchError(f"запись {key} не содержит JSON-ответ")
        return response

    def save_response(
        self,
        key: str,
        response: dict,
        *,
        service: str,
        stage: str,
    ) -> None:
        self._save_envelope(
            key,
            {
                "schema_version": 1,
                "request_sha256": key,
                "service": service,
                "stage": stage,
                "ok": True,
                "response": response,
            },
        )

    def save_error(
        self,
        key: str,
        error: Exception,
        *,
        service: str,
        stage: str,
    ) -> None:
        self._save_envelope(
            key,
            {
                "schema_version": 1,
                "request_sha256": key,
                "service": service,
                "stage": stage,
                "ok": False,
                "error_type": type(error).__name__,
                "http_status": getattr(
                    getattr(error, "response", None), "status_code", None
                ),
            },
        )

    def checkpoint(self, stage: str, value: object) -> None:
        if self.mode == "live":
            return
        event = {
            "question_id": self.current_question_id,
            "stage": stage,
            "value_sha256": _sha256(value),
        }
        if self.mode == "record":
            self.trace.append(event)
            return
        if self.replay_position >= len(self.trace):
            raise ReplayMismatchError(f"лишний checkpoint: {stage}")
        expected = self.trace[self.replay_position]
        self.replay_position += 1
        if event != expected:
            raise ReplayMismatchError(
                f"checkpoint не совпал: expected={expected.get('stage')} actual={stage}"
            )

    def finalize(self) -> None:
        if self.mode == "record":
            payload = {"schema_version": 1, "events": self.trace}
            _write_json(self.trace_path, payload)
        elif self.mode == "replay" and self.replay_position != len(self.trace):
            raise ReplayMismatchError(
                f"воспроизведено {self.replay_position} из {len(self.trace)} checkpoints"
            )

    def _save_envelope(self, key: str, envelope: dict) -> None:
        path = self.responses_dir / f"{key}.json"
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing != envelope:
                raise ReplayMismatchError(f"разные ответы для одного запроса {key}")
            return
        _write_json(path, envelope)


def _sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
