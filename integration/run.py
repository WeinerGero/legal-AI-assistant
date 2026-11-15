from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import uvicorn

from integration.config import IntegrationConfig
from submission.search import _document_hashes


def _corpus_id(corpus_path: Path) -> str:
    with zipfile.ZipFile(corpus_path) as archive:
        documents = []
        for name in sorted(item for item in archive.namelist() if not item.endswith("/")):
            hashes = _document_hashes(archive.read(name))
            documents.append(
                {
                    "name": name,
                    "raw_sha256": hashes.raw_sha256,
                    "text_sha256": hashes.text_sha256,
                }
            )
    canonical = json.dumps(documents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "corpus-sha256-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _index_generation_id(index_dir: Path) -> str:
    manifest = index_dir / "manifest.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"Index manifest is missing: {manifest}")
    return "index-manifest-sha256-" + hashlib.sha256(manifest.read_bytes()).hexdigest()


def configure_runtime_identifiers(config: IntegrationConfig) -> tuple[str, str]:
    corpus_id = _corpus_id(config.corpus_path)
    index_generation_id = _index_generation_id(config.index_dir)
    os.environ["CORPUS_ID"] = corpus_id
    os.environ["INDEX_GENERATION_ID"] = index_generation_id
    return corpus_id, index_generation_id


def main() -> None:
    configure_runtime_identifiers(IntegrationConfig.from_env())
    uvicorn.run(
        "integration.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
