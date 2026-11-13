"""Чтение базы знаний, разбиение и локальные BM25/FAISS-индексы."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import bm25s
import faiss
import numpy as np

from submission.model_client import ModelClient

CLAUSE = re.compile(r"(?m)(?=^(?:статья|раздел|глава|пункт|п\.)?\s*\d+(?:\.\d+)*[.)]?\s+)", re.I)
DATE = re.compile(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b")
RELATION = re.compile(r"(?i)\b(изменяет|дополняет|заменяет|отменяет|утратил силу)\b")


@dataclass(frozen=True, slots=True)
class Fragment:
    source: str
    section: str
    text: str
    dates: tuple[str, ...]
    relations: tuple[str, ...]


def load_fragments(path: Path, *, max_chars: int = 1800, overlap: int = 200) -> list[Fragment]:
    fragments: list[Fragment] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or Path(name).suffix.lower() not in {".txt", ".md", ".csv", ".json"}:
                continue
            raw = archive.read(name)
            text = _decode(raw)
            if Path(name).suffix.lower() == ".json":
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
            for section in filter(str.strip, CLAUSE.split(text)):
                title = section.splitlines()[0].strip()[:120]
                start = 0
                while start < len(section):
                    part = section[start : start + max_chars].strip()
                    if part:
                        fragments.append(
                            Fragment(
                                source=name,
                                section=title,
                                text=part,
                                dates=tuple(DATE.findall(part)),
                                relations=tuple(match.group(1).lower() for match in RELATION.finditer(part)),
                            )
                        )
                    if start + max_chars >= len(section):
                        break
                    start += max_chars - overlap
    if not fragments:
        raise ValueError("knowledge-base.zip не содержит поддерживаемых текстовых документов")
    return fragments


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class HybridIndex:
    def __init__(self, fragments: list[Fragment], client: ModelClient) -> None:
        self.fragments = fragments
        self.client = client
        corpus = [item.text for item in fragments]
        self.tokens = bm25s.tokenize(corpus, stopwords="ru")
        self.bm25 = bm25s.BM25()
        self.bm25.index(self.tokens)
        embeddings = np.asarray(client.embed(corpus), dtype="float32")
        faiss.normalize_L2(embeddings)
        self.faiss = faiss.IndexFlatIP(embeddings.shape[1])
        self.faiss.add(embeddings)

    def search(self, query: str, *, top_k: int = 30, final_k: int = 10) -> list[Fragment]:
        count = min(top_k, len(self.fragments))
        bm_ids, _ = self.bm25.retrieve(bm25s.tokenize([query]), k=count)
        vector = np.asarray(self.client.embed([query]), dtype="float32")
        faiss.normalize_L2(vector)
        _, vec_ids = self.faiss.search(vector, count)
        scores: dict[int, float] = {}
        for ranking in (bm_ids[0].tolist(), vec_ids[0].tolist()):
            for rank, index in enumerate(ranking, 1):
                if index >= 0:
                    scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
        candidates = sorted(scores, key=scores.get, reverse=True)[:top_k]
        reranked = self.client.rerank(query, [self.fragments[i].text for i in candidates], final_k)
        return [self.fragments[candidates[index]] for index, _score in reranked]
