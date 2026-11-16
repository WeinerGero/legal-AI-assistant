from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrationConfig:
    corpus_path: Path
    index_dir: Path
    model_timeout_seconds: float = 300.0
    request_timeout_seconds: float = 330.0
    readiness_probe_interval_seconds: float = 30.0
    readiness_retry_interval_seconds: float = 5.0
    readiness_probe_timeout_seconds: float = 10.0
    max_concurrency: int = 1
    queue_capacity: int = 4
    top_k: int = 30
    evidence_k: int = 10

    def __post_init__(self) -> None:
        if self.model_timeout_seconds <= 0:
            raise ValueError("RAG_MODEL_TIMEOUT_SECONDS must be positive")
        if not 0 < self.request_timeout_seconds < 360:
            raise ValueError("RAG_REQUEST_TIMEOUT_SECONDS must be between 0 and 360")
        if self.readiness_probe_interval_seconds <= 0:
            raise ValueError("RAG_READINESS_PROBE_INTERVAL_SECONDS must be positive")
        if self.readiness_retry_interval_seconds <= 0:
            raise ValueError("RAG_READINESS_RETRY_INTERVAL_SECONDS must be positive")
        if self.readiness_probe_timeout_seconds <= 0:
            raise ValueError("RAG_READINESS_PROBE_TIMEOUT_SECONDS must be positive")

    @classmethod
    def from_env(cls) -> "IntegrationConfig":
        return cls(
            corpus_path=Path(os.environ.get("RAG_CORPUS_PATH", "/data/knowledge-base.zip")),
            index_dir=Path(os.environ.get("RAG_INDEX_DIR", "/app/submission/assets/bge_m3")),
            model_timeout_seconds=float(os.environ.get("RAG_MODEL_TIMEOUT_SECONDS", "300")),
            request_timeout_seconds=float(os.environ.get("RAG_REQUEST_TIMEOUT_SECONDS", "330")),
            readiness_probe_interval_seconds=float(os.environ.get("RAG_READINESS_PROBE_INTERVAL_SECONDS", "30")),
            readiness_retry_interval_seconds=float(os.environ.get("RAG_READINESS_RETRY_INTERVAL_SECONDS", "5")),
            readiness_probe_timeout_seconds=float(os.environ.get("RAG_READINESS_PROBE_TIMEOUT_SECONDS", "10")),
            max_concurrency=max(1, int(os.environ.get("RAG_MAX_CONCURRENCY", "1"))),
            queue_capacity=max(1, int(os.environ.get("RAG_QUEUE_CAPACITY", "4"))),
            top_k=max(1, int(os.environ.get("RAG_TOP_K", "30"))),
            evidence_k=max(1, int(os.environ.get("RAG_EVIDENCE_K", "10"))),
        )
