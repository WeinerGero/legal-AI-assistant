"""Чтение базы знаний, разбиение и локальные BM25/FAISS-индексы."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import perf_counter

import bm25s
import faiss
import numpy as np

try:
    from submission.model_client import ModelClient
except ImportError:  # плоский корень submission.zip
    from model_client import ModelClient

CLAUSE = re.compile(r"(?m)(?=^(?:статья|раздел|глава|пункт|п\.)?\s*\d+(?:\.\d+)*[.)]?\s+)", re.I)
DATE = re.compile(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b")
RELATION = re.compile(r"(?i)\b(изменяет|дополняет|заменяет|отменяет|утратил силу)\b")
INDEX_MEMBERS = frozenset({"index.faiss", "chunks.json", "manifest.json"})


@dataclass(frozen=True, slots=True)
class Fragment:
    source: str
    section: str
    text: str
    dates: tuple[str, ...]
    relations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentHashes:
    raw_sha256: str
    text_sha256: str


def load_fragments(path: Path, *, max_chars: int = 1800, overlap: int = 200) -> list[Fragment]:
    fragments: list[Fragment] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            _validate_member_name(name)
            if (
                name.endswith("/")
                or name in INDEX_MEMBERS
                or Path(name).suffix.lower() not in {".txt", ".md", ".csv", ".json"}
            ):
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
    def archive_has_prebuilt(cls, knowledge_zip: Path) -> bool:
        with zipfile.ZipFile(knowledge_zip) as archive:
            names = set(archive.namelist())
        present = names & INDEX_MEMBERS
        if present and present != INDEX_MEMBERS:
            missing = ", ".join(sorted(INDEX_MEMBERS - present))
            raise ValueError(f"неполный индекс в корне ZIP, отсутствуют: {missing}")
        return present == INDEX_MEMBERS

    @classmethod
    def from_archive(cls, knowledge_zip: Path, client: ModelClient) -> "HybridIndex":
        """Загружает индекс из корня ZIP и сверяет SHA-256 каждого документа."""
        with zipfile.ZipFile(knowledge_zip) as archive:
            for name in archive.namelist():
                _validate_member_name(name)
            names = set(archive.namelist())
            missing = INDEX_MEMBERS - names
            if missing:
                raise ValueError(f"в корне ZIP отсутствуют: {', '.join(sorted(missing))}")
            index_bytes = archive.read("index.faiss")
            chunks_bytes = archive.read("chunks.json")
            manifest_bytes = archive.read("manifest.json")
            document_hashes = {
                name: _document_hashes(archive.read(name))
                for name in sorted(names - INDEX_MEMBERS)
                if not name.endswith("/")
            }

        fragments = _load_index_fragments(chunks_bytes)
        manifest = _load_manifest(manifest_bytes)
        instance = cls.__new__(cls)
        instance.fragments = fragments
        instance.client = client
        instance.faiss = faiss.deserialize_index(np.frombuffer(index_bytes, dtype="uint8"))
        instance._build_lexical()
        _validate_bundle(
            manifest=manifest,
            index=instance.faiss,
            fragments=fragments,
            chunks_bytes=chunks_bytes,
            embedding_model=client.embedding_model,
            document_hashes=document_hashes,
            expected_seed=client.seed,
        )
        return instance

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
        fragments = _load_index_fragments(raw_chunks)
        manifest = _load_manifest(manifest_path.read_bytes())
        document_hashes = _hash_archive_documents(knowledge_zip)

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
            embedding_model=client.embedding_model,
            document_hashes=document_hashes,
            expected_seed=client.seed,
        )
        return instance

    def search(self, query: str, *, top_k: int = 30, final_k: int = 10) -> list[Fragment]:
        count = min(top_k, len(self.fragments))
        started = perf_counter()
        try:
            bm_ids, _ = self.bm25.retrieve(bm25s.tokenize([query]), k=count)
        except Exception as exc:
            self._record_rag("bm25", started, error=exc)
            raise
        self._record_rag(
            "bm25",
            started,
            candidates_count=len({int(item) for item in bm_ids[0] if item >= 0}),
        )

        started = perf_counter()
        try:
            vector = np.asarray(self.client.embed([query]), dtype="float32")
        except Exception as exc:
            self._record_rag("embeddings", started, error=exc)
            raise
        if vector.shape != (1, self.faiss.d):
            error = ValueError(
                f"размерность запроса {vector.shape} не совпадает с FAISS (*, {self.faiss.d})"
            )
            self._record_rag("embeddings", started, error=error, request_count=1)
            raise error
        self._record_rag("embeddings", started, request_count=1, model=self.client.embedding_model)
        faiss.normalize_L2(vector)

        started = perf_counter()
        try:
            _, vec_ids = self.faiss.search(vector, count)
        except Exception as exc:
            self._record_rag("faiss", started, error=exc)
            raise
        self._record_rag(
            "faiss",
            started,
            candidates_count=len({int(item) for item in vec_ids[0] if item >= 0}),
        )

        started = perf_counter()
        scores: dict[int, float] = {}
        for ranking in (bm_ids[0].tolist(), vec_ids[0].tolist()):
            for rank, index in enumerate(ranking, 1):
                if index >= 0:
                    scores[index] = scores.get(index, 0.0) + 1.0 / (60 + rank)
        candidates = sorted(scores, key=lambda index: (-scores[index], index))[:top_k]
        self._record_rag("fusion", started, candidates_count=len(candidates))

        started = perf_counter()
        try:
            reranked = self.client.rerank(
                query, [self.fragments[i].text for i in candidates], final_k
            )
        except Exception as exc:
            self._record_rag("rerank", started, error=exc, candidates_count=len(candidates))
            raise
        result: list[Fragment] = []
        seen: set[int] = set()
        for index, _score in reranked:
            if not 0 <= index < len(candidates) or index in seen:
                raise ValueError(f"некорректный индекс reranker: {index}")
            seen.add(index)
            result.append(self.fragments[candidates[index]])
        self._record_rag(
            "rerank",
            started,
            model=self.client.reranker_model,
            candidates_count=len(candidates),
            selected_fragments_count=len(result),
            unique_sources_count=len({fragment.source for fragment in result}),
        )
        return result

    def _record_rag(
        self,
        stage: str,
        started: float,
        *,
        error: Exception | None = None,
        **fields,
    ) -> None:
        metrics = self.client.metrics
        if metrics is None:
            return
        metrics.record(
            "rag",
            stage,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            error_count=int(error is not None),
            error_type=type(error).__name__ if error is not None else None,
            **fields,
        )


def _load_index_fragments(raw: bytes) -> list[Fragment]:
    chunks_payload = json.loads(raw.decode("utf-8-sig"))
    rows = chunks_payload.get("chunks", []) if isinstance(chunks_payload, dict) else chunks_payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("chunks.json должен содержать непустой список chunks")
    return [_fragment_from_record(row) for row in rows]


def _load_manifest(raw: bytes) -> dict:
    manifest = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json должен содержать JSON-объект")
    return manifest


def _fragment_from_record(record: object) -> Fragment:
    if not isinstance(record, dict):
        raise ValueError("элемент chunks.json должен быть объектом")
    text = str(record.get("text") or record.get("search_text") or "").strip()
    if not text:
        raise ValueError("фрагмент индекса не содержит text")
    return Fragment(
        source=str(
            record.get("document") or record.get("source") or record.get("source_path") or ""
        ),
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
    embedding_model: str,
    document_hashes: dict[str, DocumentHashes],
    expected_seed: int,
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
    expected_documents_count = manifest.get("documents_count")
    if expected_documents_count is not None and int(expected_documents_count) != len(document_hashes):
        raise ValueError(
            f"число документов {len(document_hashes)} не совпадает с manifest "
            f"{expected_documents_count}"
        )
    if manifest.get("normalized") is False:
        raise ValueError("индекс manifest не нормализован для cosine/IP поиска")
    manifest_seed = manifest.get("seed")
    if manifest_seed is not None and int(manifest_seed) != expected_seed:
        raise ValueError(f"seed индекса {manifest_seed} не совпадает с runtime seed {expected_seed}")

    checks = {
        "chunks_sha256": hashlib.sha256(chunks_bytes).hexdigest(),
        "corpus_sha256": _fragments_sha256(fragments),
    }
    for name, actual in checks.items():
        expected = manifest.get(name)
        if expected is None:
            continue
        if str(expected).lower() != actual:
            raise ValueError(f"контрольная сумма {name} не совпадает")
    expected_documents = _manifest_document_hashes(manifest)
    if set(expected_documents) != set(document_hashes):
        missing = sorted(set(document_hashes) - set(expected_documents))
        extra = sorted(set(expected_documents) - set(document_hashes))
        raise ValueError(f"manifest не соответствует документам ZIP: missing={missing}, extra={extra}")
    for name, actual in document_hashes.items():
        expected = expected_documents[name]
        raw_matches = bool(expected.raw_sha256) and expected.raw_sha256.lower() == actual.raw_sha256
        text_matches = (
            bool(expected.text_sha256) and expected.text_sha256.lower() == actual.text_sha256
        )
        if not raw_matches and not text_matches:
            raise ValueError(f"SHA-256 документа не совпадает: {name}")
    referenced_documents = {item.source for item in fragments}
    if "" in referenced_documents or not referenced_documents <= set(document_hashes):
        unknown = sorted(referenced_documents - set(document_hashes))
        raise ValueError(f"chunks.json ссылается на неизвестные документы: {unknown}")


def _manifest_document_hashes(manifest: dict) -> dict[str, DocumentHashes]:
    payload = manifest.get("documents", manifest.get("document_sha256"))
    result: dict[str, DocumentHashes] = {}
    if isinstance(payload, dict):
        for name, value in payload.items():
            if isinstance(value, dict):
                raw_digest = value.get("raw_sha256") or value.get("sha256") or ""
                text_digest = value.get("text_sha256") or ""
            else:
                raw_digest = value
                text_digest = ""
            if raw_digest or text_digest:
                result[str(name)] = DocumentHashes(str(raw_digest), str(text_digest))
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = item.get("path") or item.get("name") or item.get("source")
            raw_digest = item.get("raw_sha256") or item.get("sha256") or item.get("hash") or ""
            text_digest = item.get("text_sha256") or ""
            if name and (raw_digest or text_digest):
                result[str(name)] = DocumentHashes(str(raw_digest), str(text_digest))
    if not result:
        raise ValueError("manifest не содержит SHA-256 всех документов")
    for name in result:
        _validate_member_name(name)
    return result


def _hash_archive_documents(path: Path) -> dict[str, DocumentHashes]:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            _validate_member_name(name)
        return {
            name: _document_hashes(archive.read(name))
            for name in sorted(archive.namelist())
            if not name.endswith("/")
        }


def _document_hashes(raw: bytes) -> DocumentHashes:
    text = _decode(raw).replace("\r\n", "\n").replace("\r", "\n")
    return DocumentHashes(
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or name.startswith(("/", "\\")) or "\\" in name or ".." in path.parts:
        raise ValueError(f"небезопасный путь внутри ZIP: {name!r}")


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
