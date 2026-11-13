"""Сборка конкурсного ZIP (ARCHITECTURE.md §16.2, §13.5).

``solution.py`` кладётся в корень, а пакет ``core`` сохраняет структуру,
необходимую его абсолютным импортам. Веб-зависимости в архив не попадают.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

FLATTENED_MODULES: tuple[str, ...] = (
    "core/__init__.py",
    "core/contracts.py",
    "core/pipeline.py",
    "core/context.py",
    "core/fusion.py",
    "core/analysis/__init__.py",
    "core/analysis/request.py",
    "core/analysis/validation.py",
    "core/retrieval/__init__.py",
    "core/retrieval/search.py",
    "core/retrieval/rerank.py",
    "core/retrieval/evidence.py",
    "core/retrieval/temporal.py",
    "core/generation/answer.py",
    "core/generation/repair.py",
    "core/generation/__init__.py",
    "core/verification/__init__.py",
    "core/verification/programmatic.py",
    "core/verification/semantic.py",
    "core/verification/citations.py",
    "core/prompts/__init__.py",
    "core/prompts/analysis_v1.txt",
    "core/prompts/generation_v1.txt",
    "core/prompts/verification_v1.txt",
    "core/prompts/repair_v1.txt",
    "submission/__init__.py",
    "submission/solution.py",
)


def build(zip_path: Path, repo_root: Path) -> Path:
    """Готовит воспроизводимый архив ядра и конкурсной точки входа."""
    root = repo_root.resolve()
    sources = collect_sources(root)
    missing = [path for path in sources if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(root)) for path in missing)
        raise FileNotFoundError(f"не найдены файлы конкурсной сборки: {names}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(sources, key=lambda item: item.as_posix()):
            relative = source.relative_to(root).as_posix()
            target = "solution.py" if relative == "submission/solution.py" else relative
            info = archive.getinfo(target) if target in archive.namelist() else None
            if info is not None:
                raise ValueError(f"повторный путь в архиве: {target}")
            archive.write(source, target)
    return zip_path


def collect_sources(repo_root: Path) -> list[Path]:
    return [repo_root / relative for relative in FLATTENED_MODULES]
