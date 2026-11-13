"""Сборка конкурсного ZIP (ARCHITECTURE.md §16.2, §13.5).

Конкурсная сборка кладёт самодостаточные модули из ``submission/`` и явно
перечисленные инструкции моделей в корень архива. Веб-специфические модули
``core.*`` и библиотеки в сборку не попадают.
"""

from __future__ import annotations

from pathlib import Path
import zipfile

FIXED_ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)

FLATTENED_MODULES: tuple[str, ...] = (
    "core/reproducibility.py",
    "submission/solution.py",
    "submission/model_client.py",
    "submission/metrics.py",
    "submission/search.py",
    "submission/pipeline.py",
    "submission/recording.py",
)

PROMPT_FILES: tuple[str, ...] = (
    "core/prompts/analysis_v1.txt",
    "core/prompts/generation_v1.txt",
    "core/prompts/repair_v1.txt",
    "core/prompts/verification_v1.txt",
)

ASSET_FILES: dict[str, str] = {
    "requirements.txt": "submission/assets/requirements.txt",
    "index.faiss": "submission/assets/bge_m3/index.faiss",
    "chunks.json": "submission/assets/bge_m3/chunks.json",
    "manifest.json": "submission/assets/bge_m3/manifest.json",
}


def build(zip_path: Path, repo_root: Path) -> Path:
    """Готовит архив с плоской структурой и проверяет полный манифест."""
    sources = collect_sources(repo_root)
    assets = [(name, repo_root / relative) for name, relative in ASSET_FILES.items()]
    missing = [str(path) for path in [*sources, *(path for _name, path in assets)] if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"не найдены файлы сборки: {', '.join(missing)}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    entries = [(source.name, source.read_bytes()) for source in sources]
    entries.extend((name, path.read_bytes()) for name, path in assets)
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name, content in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    return zip_path


def collect_sources(repo_root: Path) -> list[Path]:
    return [repo_root / relative for relative in (*FLATTENED_MODULES, *PROMPT_FILES)]
