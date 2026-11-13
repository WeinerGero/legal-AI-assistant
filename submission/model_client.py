"""HTTP-клиент совместимого с OpenAI конкурсного прокси."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import httpx

try:
    from core.reproducibility import DEFAULT_SEED
except ImportError:  # плоский корень submission.zip
    from reproducibility import DEFAULT_SEED


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

    def _request(
        self,
        endpoint: str,
        payload: dict,
        *,
        base_url: str,
        api_key: str,
        service: str,
    ) -> dict:
        if not base_url or not api_key:
            raise RuntimeError(f"не заданы URL или ключ API для {service}")
        response = httpx.post(
            f"{base_url}/{endpoint.lstrip('/')}",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def complete_json(self, system_prompt: str, payload: str) -> str:
        if not self.llm_model:
            raise RuntimeError("не задана модель генерации")
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
        )
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        return str(content)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        data = self._request(
            "embeddings",
            {"model": self.embedding_model, "input": list(texts)},
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            service="embedder",
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
        )
        rows = data.get("results", data.get("data", []))
        return [
            (int(row.get("index", row.get("document_index"))), row.get("relevance_score", row.get("score")))
            for row in rows
        ]
