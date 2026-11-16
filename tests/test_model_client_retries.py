from __future__ import annotations

import httpx

from submission.model_client import ModelClient


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class _UnavailableResponse:
    status_code = 503
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        raise httpx.HTTPStatusError("temporary outage", request=request, response=self)


def test_read_timeout_is_retried_within_bound(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-placeholder")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("API_MODE", "live")
    monkeypatch.setattr("submission.model_client.sleep", lambda _seconds: None)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("bounded timeout")
        return _Response()

    monkeypatch.setattr("submission.model_client.httpx.post", post)
    client = ModelClient(timeout=1.0)
    assert client.complete_json("system", "payload", stage="verification") == "{}"
    assert calls == 2
    assert client.question_trace()["retries"] == 1


def test_deepseek_503_is_retried_then_recovers(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-placeholder")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("API_MODE", "live")
    monkeypatch.setattr("submission.model_client.sleep", lambda _seconds: None)
    calls = 0

    def post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _UnavailableResponse() if calls == 1 else _Response()

    monkeypatch.setattr("submission.model_client.httpx.post", post)
    client = ModelClient(timeout=1.0)
    assert client.complete_json("system", "payload", stage="generation") == "{}"
    assert calls == 2
    assert client.question_trace()["retries"] == 1

