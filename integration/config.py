from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IntegrationConfig:
    corpus_path: Path
    index_dir: Path
    model_timeout_seconds: float = 300.0
    max_concurrency: int = 1
    queue_capacity: int = 4
    top_k: int = 30
    evidence_k: int = 10

    @classmethod
    def from_env(cls) -> "IntegrationConfig":
        return cls(
            corpus_path=Path(os.environ.get("RAG_CORPUS_PATH", "/data/knowledge-base.zip")),
            index_dir=Path(os.environ.get("RAG_INDEX_DIR", "/app/submission/assets/bge_m3")),
            model_timeout_seconds=float(os.environ.get("RAG_MODEL_TIMEOUT_SECONDS", "300")),
            max_concurrency=max(1, int(os.environ.get("RAG_MAX_CONCURRENCY", "1"))),
            queue_capacity=max(1, int(os.environ.get("RAG_QUEUE_CAPACITY", "4"))),
            top_k=max(1, int(os.environ.get("RAG_TOP_K", "30"))),
            evidence_k=max(1, int(os.environ.get("RAG_EVIDENCE_K", "10"))),
        )
