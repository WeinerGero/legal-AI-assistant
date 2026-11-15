from __future__ import annotations

import zipfile

from submission.search import load_fragments


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

