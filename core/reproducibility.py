"""Единая настройка воспроизводимости проекта."""

from __future__ import annotations

import os
import random

import numpy as np

DEFAULT_SEED = 24


def configure_reproducibility(seed: int = DEFAULT_SEED) -> int:
    """Фиксирует доступные источники псевдослучайности и возвращает seed."""
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    return seed
