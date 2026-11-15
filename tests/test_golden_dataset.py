from __future__ import annotations

import hashlib
import json
from pathlib import Path


GOLDEN = Path(__file__).parent / "golden" / "PSB_golden_dataset_v1.1_codex.json"
PARTITIONS = Path(__file__).parent / "golden" / "golden_partitions.json"
EXPECTED_SHA256 = "dc4143f78f9978217135e66208e94102fed90e2b6ef858cd2d6f3a8ed062da13"
REQUIRED_FIELDS = {
    "id",
    "category",
    "title",
    "query",
    "expected_behavior",
    "gold_answer",
    "failure_conditions",
}


def test_golden_dataset_is_exact_attachment() -> None:
    assert hashlib.sha256(GOLDEN.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_golden_dataset_has_65_unique_complete_cases() -> None:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    assert len(cases) == 65
    assert len({case["id"] for case in cases}) == 65
    assert all(REQUIRED_FIELDS <= case.keys() for case in cases)


def test_partition_manifest_assigns_every_case_once() -> None:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = payload.get("cases", payload)
    manifest = json.loads(PARTITIONS.read_text(encoding="utf-8-sig"))
    assigned = [case_id for part in manifest["partitions"] for case_id in part["case_ids"]]
    assert manifest["dataset_sha256"] == EXPECTED_SHA256
    assert len(manifest["partitions"]) == 13
    assert all(len(part["case_ids"]) == 5 for part in manifest["partitions"])
    assert len(assigned) == len(set(assigned)) == 65
    assert set(assigned) == {case["id"] for case in cases}

