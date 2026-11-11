"""Промты трёх вызовов DeepSeek и исправления (ARCHITECTURE.md §6.3, §8.4, §9.4).

Файлы рядом содержат дословные системные инструкции из архитектуры.
Версии промтов фиксируются в ``prompt_versions`` журнала запуска (§16.3).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

PROMPTS: dict[str, str] = {
    "analysis_v1": "analysis_v1.txt",
    "generation_v1": "generation_v1.txt",
    "verification_v1": "verification_v1.txt",
    "repair_v1": "repair_v1.txt",
}


@lru_cache(maxsize=None)
def load_prompt(prompt_id: str) -> str:
    """Возвращает текст системной инструкции по её идентификатору версии."""
    try:
        filename = PROMPTS[prompt_id]
    except KeyError as exc:
        raise KeyError(f"неизвестный промт: {prompt_id}") from exc
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")
