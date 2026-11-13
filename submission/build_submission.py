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
    "submission/search.py",
    "submission/pipeline.py",
)

PROMPT_FILES: tuple[str, ...] = (
    "core/prompts/analysis_v1.txt",
    "core/prompts/generation_v1.txt",
    "core/prompts/repair_v1.txt",
    "core/prompts/verification_v1.txt",
)

REQUIREMENTS = """httpx==0.27.2
numpy==1.26.4
bm25s==0.2.14
faiss-cpu==1.16.0
"""


def build(zip_path: Path, repo_root: Path) -> Path:
    """Готовит архив с плоской структурой и проверяет полный манифест."""
    sources = collect_sources(repo_root)
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"не найдены файлы сборки: {', '.join(missing)}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    entries = [(source.name, source.read_bytes()) for source in sources]
    entries.append(("requirements.txt", REQUIREMENTS.encode("utf-8")))
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
