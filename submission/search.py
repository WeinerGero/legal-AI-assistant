"""Чтение базы знаний, разбиение и локальные BM25/FAISS-индексы."""

from __future__ import annotations

import hashlib
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
        self._build_lexical()
        corpus = [item.text for item in self.fragments]
        embeddings = np.asarray(client.embed(corpus), dtype="float32")
        if embeddings.ndim != 2 or embeddings.shape[0] != len(self.fragments):
            raise ValueError("API эмбеддингов вернул несовместимую матрицу")
        faiss.normalize_L2(embeddings)
        self.faiss = faiss.IndexFlatIP(embeddings.shape[1])
        self.faiss.add(embeddings)

    def _build_lexical(self) -> None:
        corpus = [item.text for item in self.fragments]
        self.tokens = bm25s.tokenize(corpus, stopwords="ru")
        self.bm25 = bm25s.BM25()
        self.bm25.index(self.tokens)

    @classmethod
    def from_prebuilt(
        cls, index_dir: Path, knowledge_zip: Path, client: ModelClient
    ) -> "HybridIndex":
        """Загружает согласованный набор ``FAISS + chunks + manifest``."""
        index_path = index_dir / "index.faiss"
        chunks_path = index_dir / "chunks.json"
        manifest_path = index_dir / "manifest.json"
        for path in (index_path, chunks_path, manifest_path, knowledge_zip):
            if not path.is_file():
                raise FileNotFoundError(f"не найден обязательный файл индекса: {path.name}")

        raw_chunks = chunks_path.read_bytes()
        chunks_payload = json.loads(raw_chunks)
        rows = chunks_payload.get("chunks", []) if isinstance(chunks_payload, dict) else chunks_payload
        if not isinstance(rows, list) or not rows:
            raise ValueError("chunks.json должен содержать непустой список chunks")
        fragments = [_fragment_from_record(row) for row in rows]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json должен содержать JSON-объект")

        instance = cls.__new__(cls)
        instance.fragments = fragments
        instance.client = client
        instance.faiss = faiss.read_index(str(index_path))
        instance._build_lexical()
        _validate_bundle(
            manifest=manifest,
            index=instance.faiss,
            fragments=fragments,
            chunks_bytes=raw_chunks,
            knowledge_zip=knowledge_zip,
            embedding_model=client.embedding_model,
        )
        return instance

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


def _fragment_from_record(record: object) -> Fragment:
    if not isinstance(record, dict):
        raise ValueError("элемент chunks.json должен быть объектом")
    text = str(record.get("text") or record.get("search_text") or "").strip()
    if not text:
        raise ValueError("фрагмент индекса не содержит text")
    return Fragment(
        source=str(record.get("source") or record.get("source_path") or ""),
        section=str(record.get("section") or record.get("section_number") or ""),
        text=text,
        dates=tuple(str(item) for item in record.get("dates", [])),
        relations=tuple(str(item) for item in record.get("relations", [])),
    )


def _validate_bundle(
    *,
    manifest: dict,
    index: faiss.Index,
    fragments: list[Fragment],
    chunks_bytes: bytes,
    knowledge_zip: Path,
    embedding_model: str,
) -> None:
    """Отклоняет индекс, который не соответствует текстам или ZIP корпуса."""
    expected_count = int(manifest.get("chunk_count", manifest.get("chunks_count", len(fragments))))
    if expected_count != len(fragments) or index.ntotal != len(fragments):
        raise ValueError(
            f"число фрагментов не совпадает: manifest={expected_count}, "
            f"chunks={len(fragments)}, faiss={index.ntotal}"
        )
    expected_dimension = manifest.get("dimension", manifest.get("embedding_dimension"))
    if expected_dimension is not None and int(expected_dimension) != index.d:
        raise ValueError(f"размерность FAISS {index.d} не совпадает с manifest {expected_dimension}")
    expected_model = manifest.get("embedding_model") or manifest.get("model")
    if expected_model and str(expected_model) != embedding_model:
        raise ValueError(
            f"модель индекса {expected_model!r} не совпадает с EMB_MODEL {embedding_model!r}"
        )

    checks = {
        "knowledge_base_sha256": _sha256_file(knowledge_zip),
        "archive_sha256": _sha256_file(knowledge_zip),
        "chunks_sha256": hashlib.sha256(chunks_bytes).hexdigest(),
        "corpus_sha256": _fragments_sha256(fragments),
    }
    declared = 0
    for name, actual in checks.items():
        expected = manifest.get(name)
        if expected is None:
            continue
        declared += 1
        if str(expected).lower() != actual:
            raise ValueError(f"контрольная сумма {name} не совпадает")
    if declared == 0:
        raise ValueError("manifest не содержит SHA-256 для проверки корпуса или фрагментов")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fragments_sha256(fragments: list[Fragment]) -> str:
    payload = [
        {
            "source": item.source,
            "section": item.section,
            "text": item.text,
            "dates": item.dates,
            "relations": item.relations,
        }
        for item in fragments
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
