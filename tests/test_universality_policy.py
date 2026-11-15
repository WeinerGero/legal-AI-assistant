from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY_FILES = (
    ROOT / ".agents",
    ROOT / "AGENTS.md",
    ROOT / ".github" / "instructions" / "agents-boundaries.instructions.md",
    ROOT / "docs" / "ARCHITECTURE.md",
)
RUNTIME_FILES = tuple(
    ROOT / "submission" / name
    for name in ("pipeline.py", "search.py", "model_client.py", "solution.py")
) + tuple((ROOT / "core" / "prompts").glob("*.txt"))


@pytest.mark.parametrize("path", POLICY_FILES, ids=lambda path: path.name)
def test_universality_is_an_explicit_repository_rule(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig").casefold()
    assert "универс" in text
    assert "id" in text
    assert "эталон" in text


@pytest.mark.parametrize("path", RUNTIME_FILES, ids=lambda path: path.name)
def test_runtime_has_no_golden_case_id_branches(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    assert not re.search(
        r"(?:question|case|query)_?id\s*(?:==|in)\s*(?:[\"'](?:H1|D7|R3|C1|Calc3)[\"']|[\[{(])",
        text,
        re.IGNORECASE,
    )
    assert "PSB_golden_dataset" not in text


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("ООО Альфа, 01.02.2025, 100000 руб.", "ООО Бета, 03.04.2026, 275000 руб."),
        ("редакция от 01.01.2024", "редакция от 02.02.2025"),
        ("пункт 4.2 затем пункт 7.1", "пункт 7.1 затем пункт 4.2"),
    ],
)
def test_metamorphic_inputs_do_not_select_case_specific_code(source: str, replacement: str) -> None:
    joined = "\n".join(path.read_text(encoding="utf-8-sig") for path in RUNTIME_FILES)
    assert source not in joined
    assert replacement not in joined
