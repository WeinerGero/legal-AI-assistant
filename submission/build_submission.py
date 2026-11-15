"""Deterministic, flat and self-contained competition archive builder."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import Path

FIXED_ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)
MODULES = (
    "submission/budget.py",
    "submission/reproducibility.py",
    "submission/solution.py",
    "submission/model_client.py",
    "submission/metrics.py",
    "submission/search.py",
    "submission/pipeline.py",
    "submission/recording.py",
)
PROMPT_FILES = (
    "core/prompts/analysis_v2.txt",
    "core/prompts/generation_v2.txt",
    "core/prompts/verification_v2.txt",
)
INDEX_FILES = ("index.faiss", "chunks.json", "manifest.json")
REQUIRED_PACKAGES = {"httpx", "numpy", "faiss-cpu", "bm25s"}


def build(zip_path: Path, repo_root: Path) -> Path:
    entries = {
        Path(relative).name: (repo_root / relative).read_bytes()
        for relative in MODULES
    }
    entries["embedded_index.py"] = generate_embedded_index(repo_root)
    entries["prompts.py"] = generate_prompts(repo_root)
    entries["requirements.txt"] = generate_requirements(repo_root)
    if any(not (name.endswith(".py") or name == "requirements.txt") for name in entries):
        raise ValueError("competition ZIP may contain only root Python files and requirements.txt")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(_zip_info(name), content, compresslevel=9)
    return zip_path


def generate_embedded_index(repo_root: Path) -> bytes:
    assets = repo_root / "submission" / "assets" / "bge_m3"
    packed_buffer = io.BytesIO()
    with zipfile.ZipFile(packed_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in INDEX_FILES:
            path = assets / name
            if not path.is_file():
                raise FileNotFoundError(path)
            archive.writestr(_zip_info(name), path.read_bytes(), compresslevel=9)
    packed = packed_buffer.getvalue()
    encoded = base64.b85encode(packed).decode("ascii")
    chunks = "\n".join(
        f"    {encoded[position:position + 120]!r}"
        for position in range(0, len(encoded), 120)
    )
    source = f'''from __future__ import annotations
import base64
import hashlib
import io
from pathlib import Path
from zipfile import ZipFile

BUNDLE_SHA256 = {hashlib.sha256(packed).hexdigest()!r}
BUNDLE = (\n{chunks}\n)

def unpack(directory: Path) -> Path:
    packed = base64.b85decode(BUNDLE)
    if hashlib.sha256(packed).hexdigest() != BUNDLE_SHA256:
        raise ValueError("embedded index checksum mismatch")
    directory.mkdir(parents=True, exist_ok=True)
    with ZipFile(io.BytesIO(packed)) as archive:
        for name in {INDEX_FILES!r}:
            (directory / name).write_bytes(archive.read(name))
    return directory
'''
    return source.encode("utf-8")


def generate_prompts(repo_root: Path) -> bytes:
    prompts = {
        Path(relative).name: (repo_root / relative).read_text(encoding="utf-8")
        for relative in PROMPT_FILES
    }
    return ("PROMPTS = " + repr(dict(sorted(prompts.items()))) + "\n").encode("utf-8")


def generate_requirements(repo_root: Path) -> bytes:
    official = repo_root / "submission" / "assets" / "requirements.txt"
    selected = []
    for line in official.read_text(encoding="utf-8").splitlines():
        package = line.partition("==")[0].strip().casefold()
        if package in REQUIRED_PACKAGES:
            selected.append(line.strip())
    found = {line.partition("==")[0].casefold() for line in selected}
    if found != REQUIRED_PACKAGES:
        raise ValueError(f"official requirements are missing: {sorted(REQUIRED_PACKAGES - found)}")
    return ("\n".join(selected) + "\n").encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info
