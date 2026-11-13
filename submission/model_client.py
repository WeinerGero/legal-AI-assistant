"""HTTP-клиент совместимого с OpenAI конкурсного прокси."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

try:
    from core.reproducibility import DEFAULT_SEED
    from submission.recording import APIRecordingStore
except ImportError:  # плоский корень submission.zip
    from reproducibility import DEFAULT_SEED
    from recording import APIRecordingStore


def _load_local_settings() -> dict[str, str]:
    """Читает только нужные имена из локального .env, не выводя значения."""
    allowed = {
        "LLM_DEV",
        "BASE_URL",
        "LLM_BASE_URL",
        "EMBEDDER_BASE_URL",
        "RERANKER_BASE_URL",
        "LLM_API_KEY",
        "EMBEDDER_API_KEY",
        "RERANKER_API_KEY",
        "LLM_MODEL",
        "EMB_MODEL",
        "RERANKER_MODEL",
    }
    result: dict[str, str] = {}
    try:
        lines = Path(".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if name in allowed:
            result[name] = value.strip().strip("'\"")
    return result


class ModelClient:
    def __init__(
        self,
        *,
        timeout: float = 90.0,
        seed: int = DEFAULT_SEED,
        max_tokens: int = 1_500,
        metrics: Any | None = None,
        recording: APIRecordingStore | None = None,
    ) -> None:
        local = _load_local_settings()
        def get(name: str, default: str = "") -> str:
            return os.getenv(name) or local.get(name, default)

        local_key = get("LLM_DEV")
        common_base_url = get("BASE_URL")
        self.base_url = (
            get("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or common_base_url
            or ("https://matreshkaapi.mooo.com/v1" if local_key else "")
        ).rstrip("/")
        self.api_key = get("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or local_key
        self.embedding_base_url = (
            get("EMBEDDER_BASE_URL") or common_base_url or self.base_url
        ).rstrip("/")
        self.reranker_base_url = (
            get("RERANKER_BASE_URL") or common_base_url or self.base_url
        ).rstrip("/")
        self.embedding_api_key = get("EMBEDDER_API_KEY") or self.api_key
        self.reranker_api_key = get("RERANKER_API_KEY") or self.api_key
        self.llm_model = (
            get("LLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("GEN_MODEL")
            or ("deepseek-v4.1-flash" if local_key else "")
        )
        self.embedding_model = get("EMB_MODEL", "BAAI/bge-m3")
        self.reranker_model = get("RERANKER_MODEL")
        self.timeout = timeout
        self.seed = seed
        self.max_tokens = max_tokens
        self.metrics = metrics
        self.recording = recording or APIRecordingStore.from_env()

    def _request(
        self,
        endpoint: str,
        payload: dict,
        *,
        base_url: str,
        api_key: str,
        service: str,
        stage: str,
    ) -> dict:
        key = self.recording.request_key(
            service=service,
            stage=stage,
            endpoint=endpoint,
            payload=payload,
        )
        if self.recording.mode == "replay":
            return self.recording.load(key)
        try:
            if not base_url or not api_key:
                raise RuntimeError(f"не заданы URL или ключ API для {service}")
            response = httpx.post(
                f"{base_url}/{endpoint.lstrip('/')}",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError(f"API {service} вернул JSON не в виде объекта")
        except Exception as exc:
            if self.recording.mode == "record":
                self.recording.save_error(key, exc, service=service, stage=stage)
            raise
        if self.recording.mode == "record":
            self.recording.save_response(key, data, service=service, stage=stage)
        return data

    def complete_json(
        self, system_prompt: str, payload: str, *, stage: str = "unspecified"
    ) -> str:
        started = perf_counter()
        if not self.llm_model:
            error = RuntimeError("не задана модель генерации")
            self._record_llm(stage, started, None, error)
            raise error
        try:
            data = self._request(
                "chat/completions",
                {
                    "model": self.llm_model,
                    "temperature": 0,
                    "seed": self.seed,
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": payload},
                    ],
                },
                base_url=self.base_url,
                api_key=self.api_key,
                service="LLM",
                stage=stage,
            )
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            self._record_llm(stage, started, None, exc)
            raise
        self._record_llm(stage, started, data.get("usage"), None)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        return str(content)

    def _record_llm(
        self,
        stage: str,
        started: float,
        usage: object,
        error: Exception | None,
    ) -> None:
        if self.metrics is None:
            return
        usage_data = usage if isinstance(usage, dict) else {}
        self.metrics.record(
            "llm",
            stage,
            model=self.llm_model,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            request_count=1,
            error_count=int(error is not None),
            retry_count=0,
            error_type=type(error).__name__ if error is not None else None,
            http_status=getattr(getattr(error, "response", None), "status_code", None),
            prompt_tokens=usage_data.get("prompt_tokens"),
            completion_tokens=usage_data.get("completion_tokens"),
            total_tokens=usage_data.get("total_tokens"),
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        data = self._request(
            "embeddings",
            {"model": self.embedding_model, "input": list(texts)},
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            service="embedder",
            stage="embeddings",
        )
        ordered = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float | None]]:
        if not self.reranker_model:
            raise RuntimeError("не задан RERANKER_MODEL")
        data = self._request(
            "rerank",
            {
                "model": self.reranker_model,
                "query": query,
                "documents": list(documents),
                "top_n": min(top_n, len(documents)),
            },
            base_url=self.reranker_base_url,
            api_key=self.reranker_api_key,
            service="reranker",
            stage="rerank",
        )
        rows = data.get("results", data.get("data", []))
        return [
            (int(row.get("index", row.get("document_index"))), row.get("relevance_score", row.get("score")))
            for row in rows
        ]
