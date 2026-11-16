from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import submission.evaluate_golden as golden_runner
from submission.evaluate_quality import labeled_ranking_metrics


GOLDEN = Path(__file__).parent / "golden" / "PSB_golden_dataset_v1.1_codex.json"
PARTITIONS = Path(__file__).parent / "golden" / "golden_partitions.json"
P03_RELEVANCE = Path(__file__).parent / "golden" / "p03_relevance_v1.json"
HISTORY_RUNS = Path(__file__).parent / "golden" / "history_runs.json"
CONTROLLED_VERIFICATION = Path(__file__).parent / "golden" / "controlled_verification_cases.json"
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


def test_p03_relevance_labels_resolve_to_index_and_are_evaluation_only() -> None:
    annotation = json.loads(P03_RELEVANCE.read_text(encoding="utf-8"))
    chunks = json.loads((Path("submission/assets/bge_m3/chunks.json")).read_text(encoding="utf-8"))
    rows = chunks.get("chunks", []) if isinstance(chunks, dict) else chunks
    available = {row["chunk_id"] for row in rows}
    assert {row["question_id"] for row in annotation["cases"]} == {"H3", "P2", "R5", "C3", "Fx2"}
    assert all(
        fragment["chunk_id"] in available
        for case in annotation["cases"]
        for fragment in case["fragments"]
    )
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("submission/pipeline.py"), Path("submission/search.py"), Path("submission/model_client.py"))
    )
    assert "p03_relevance_v1" not in runtime_sources


def test_history_manifest_keeps_partitions_versioned_and_distinct() -> None:
    payload = json.loads(HISTORY_RUNS.read_text(encoding="utf-8"))
    rows = payload["partitions"]
    assert [row["partition_id"] for row in rows] == ["P01", "P02", "P03"]
    assert len({row["run_id"] for row in rows}) == len(rows)
    assert all(len(row["commit"]) == 40 for row in rows)


def test_controlled_verification_set_is_complete_and_independent() -> None:
    payload = json.loads(CONTROLLED_VERIFICATION.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert len(cases) == 8
    assert len({case["id"] for case in cases}) == 8
    assert {case["verdict"] for case in cases} == {"pass", "revision_required"}
    assert "DeepSeek" not in payload["methodology"]


def test_partition_persists_each_case_before_a_later_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_ids = [f"T{number}" for number in range(1, 6)]
    cases = [{"id": case_id, "query": f"question {case_id}"} for case_id in case_ids]
    manifest = {"dataset_sha256": "test", "partitions": [{"partition_id": "P01", "case_ids": case_ids}]}
    monkeypatch.setattr(golden_runner, "_ensure_run_metadata", lambda *_args: None)
    attempts: list[str] = []

    def interrupted_execute(selected: list[dict], _directory: Path):
        case_id = str(selected[0]["id"])
        attempts.append(case_id)
        if case_id == "T2":
            raise RuntimeError("interrupted")
        return (
            [{"id": case_id, "answer": f"answer {case_id}"}],
            [{"kind": "outcome", "stage": "question", "question_id": case_id,
              "final_state": "success", "duration_ms": 1.0, "error_type": None}],
            1.0,
        )

    monkeypatch.setattr(golden_runner, "_execute", interrupted_execute)
    with pytest.raises(RuntimeError, match="interrupted"):
        golden_runner._run_partition("P01", manifest, cases, tmp_path)
    saved = golden_runner._read_answer_rows(tmp_path / "partitions" / "P01" / "answers.csv")
    assert set(saved) == {"T1"}

    def resumed_execute(selected: list[dict], _directory: Path):
        case_id = str(selected[0]["id"])
        attempts.append(case_id)
        return (
            [{"id": case_id, "answer": f"answer {case_id}"}],
            [{"kind": "outcome", "stage": "question", "question_id": case_id,
              "final_state": "success", "duration_ms": 1.0, "error_type": None}],
            1.0,
        )

    monkeypatch.setattr(golden_runner, "_execute", resumed_execute)
    golden_runner._run_partition("P01", manifest, cases, tmp_path)
    saved = golden_runner._read_answer_rows(tmp_path / "partitions" / "P01" / "answers.csv")
    assert set(saved) == set(case_ids)
    assert attempts.count("T1") == 1


@pytest.mark.parametrize(
    ("ranking", "expected_recall"),
    [(["a", "b", "c"], 1.0), (["x", "y"], 0.0)],
)
def test_metric_calculator_handles_ideal_and_empty_rankings(ranking: list[str], expected_recall: float) -> None:
    result = labeled_ranking_metrics(ranking, {"a": 1, "b": 1}, 3)
    assert result["recall"] == expected_recall
    assert 0.0 <= result["precision"] <= 1.0


def test_metric_calculator_is_deterministic_with_duplicate_results() -> None:
    first = labeled_ranking_metrics(["a", "a", "b"], {"a": 1, "b": 1}, 3)
    second = labeled_ranking_metrics(["a", "a", "b"], {"a": 1, "b": 1}, 3)
    assert first == second


def test_quality_metrics_gate_writes_replay_artifacts(tmp_path: Path) -> None:
    payload = golden_runner._write_quality_metrics_at(
        tmp_path,
        [
            {"kind": "outcome", "stage": "question", "question_id": "Q1"},
            *[
                {"kind": "rag", "stage": stage, "question_id": "Q1"}
                for stage in ("bm25", "faiss", "fusion", "rerank", "evidence_selection")
            ],
        ],
    )
    assert payload["status"] == "complete"
    assert (tmp_path / "quality_metrics.json").is_file()
    assert (tmp_path / "quality_metrics.md").is_file()

