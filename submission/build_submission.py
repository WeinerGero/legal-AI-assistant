"""Сборка конкурсного ZIP (ARCHITECTURE.md §16.2, §13.5).

Конкурсная сборка **выпрямляет** необходимые модули из ``core/`` в корневые
``.py`` своего архива; вложенные каталоги для импортируемых модулей не нужны.
Веб-специфические библиотеки в сборку не попадают.
"""

from __future__ import annotations

from pathlib import Path
import zipfile

FLATTENED_MODULES: tuple[str, ...] = (
    "submission/solution.py",
    "submission/model_client.py",
    "submission/search.py",
    "submission/pipeline.py",
)

REQUIREMENTS = """httpx==0.28.1
numpy==1.26.4
bm25s==0.2.14
faiss-cpu==1.8.0.post1
"""


def build(zip_path: Path, repo_root: Path) -> Path:
    """Готовит архив с плоской структурой модулей ядра и ``solution.py``."""
    sources = collect_sources(repo_root)
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"не найдены файлы сборки: {', '.join(missing)}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            archive.write(source, source.name)
        archive.writestr("requirements.txt", REQUIREMENTS)
        prompts = repo_root / "core" / "prompts"
        for prompt in sorted(prompts.glob("*_v1.txt")):
            archive.write(prompt, prompt.name)
    return zip_path


def collect_sources(repo_root: Path) -> list[Path]:
    return [repo_root / relative for relative in FLATTENED_MODULES]
