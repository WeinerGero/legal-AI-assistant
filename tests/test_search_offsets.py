from __future__ import annotations

import zipfile

from submission.search import _fuse_rankings, load_fragments


def test_fallback_chunk_offsets_reproduce_source_text(tmp_path) -> None:
    source = "Преамбула\n\n1. Первый пункт договора.\nПродолжение.\n\n2. Второй пункт."
    archive = tmp_path / "knowledge-base.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("contract.txt", source.encode("utf-8"))

    fragments = load_fragments(archive, max_chars=32, overlap=8)

    assert fragments
    assert all(item.start_char is not None and item.end_char is not None for item in fragments)
    assert all(source[item.start_char : item.end_char] == item.text for item in fragments)
    assert any(item.section.startswith("1.") for item in fragments)
    assert any(item.section.startswith("2.") for item in fragments)


def test_rrf_variants_are_deterministic_and_weighted(monkeypatch) -> None:
    bm = [[1, 2, 3]]
    faiss = [[3, 4, 5]]
    monkeypatch.setenv("RRF_MODE", "weighted")
    monkeypatch.setenv("RRF_BM25_WEIGHT", "0.1")
    monkeypatch.setenv("RRF_FAISS_WEIGHT", "0.9")
    assert _fuse_rankings(bm, faiss, 3)[0] == 3
    monkeypatch.setenv("RRF_MODE", "faiss")
    assert _fuse_rankings(bm, faiss, 3) == [3, 4, 5]
    monkeypatch.setenv("RRF_MODE", "diverse")
    assert _fuse_rankings(bm, faiss, 4) == [1, 3, 2, 4]


def test_rrf_default_preserves_faiss_recall(monkeypatch) -> None:
    monkeypatch.delenv("RRF_MODE", raising=False)
    assert _fuse_rankings([1, 2], [3, 4, 5], 3) == [3, 4, 5]

