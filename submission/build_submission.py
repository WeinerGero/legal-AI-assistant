"""Сборка конкурсного ZIP (ARCHITECTURE.md §16.2, §13.5).

Конкурсная сборка **выпрямляет** необходимые модули из ``core/`` в корневые
``.py`` своего архива; вложенные каталоги для импортируемых модулей не нужны.
Веб-специфические библиотеки в сборку не попадают.
"""

from __future__ import annotations

from pathlib import Path

FLATTENED_MODULES: tuple[str, ...] = (
    "core/contracts.py",
    "core/pipeline.py",
    "core/context.py",
    "core/fusion.py",
    "core/retrieval/search.py",
    "core/retrieval/rerank.py",
    "core/retrieval/evidence.py",
    "core/retrieval/temporal.py",
    "core/generation/answer.py",
    "core/generation/repair.py",
    "core/verification/programmatic.py",
    "core/verification/semantic.py",
    "core/verification/citations.py",
    "submission/solution.py",
)


def build(zip_path: Path, repo_root: Path) -> Path:
    """Готовит архив с плоской структурой модулей ядра и ``solution.py``."""
    raise NotImplementedError("zipfile-сборка с выпрямлением модулей")


def collect_sources(repo_root: Path) -> list[Path]:
    return [repo_root / relative for relative in FLATTENED_MODULES]
