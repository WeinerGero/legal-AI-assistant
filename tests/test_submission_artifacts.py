from __future__ import annotations

import json

import pytest

from submission.pipeline import SubmissionPipeline
from submission.search import Fragment


class Recording:
    mode = "live"

    def checkpoint(self, _name: str, _value: object) -> None:
        pass


class ScriptedClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.recording = Recording()
        self.metrics = None
        self.budget = None

    def complete_json(self, _system: str, _user: str, *, stage: str) -> str:
        if not self.responses:
            raise AssertionError(f"unexpected model call: {stage}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response, ensure_ascii=False)


class SyntheticIndex:
    def __init__(self, batches: list[list[Fragment]]) -> None:
        self.batches = list(batches)

    def search_queries(self, *_args: object, **_kwargs: object) -> list[Fragment]:
        return self.batches.pop(0)

    @staticmethod
    def verified_citation_section(fragment: Fragment) -> str:
        return fragment.citation_section or fragment.section


def fragment(source: str, section: str, text: str, start: int) -> Fragment:
    return Fragment(
        source=source,
        section=section,
        text=text,
        dates=(),
        relations=(),
        start_char=start,
        end_char=start + len(text),
        citation_section=section,
    )


def analysis() -> dict[str, object]:
    return {
        "target_language": "ru",
        "standalone_question": "Каков срок?",
        "entities": {},
        "requested_facts": [],
        "lexical_queries": ["срок"],
        "semantic_queries": ["срок исполнения"],
        "unresolved_ambiguities": [],
    }


def generation(text: str, evidence_id: str) -> dict[str, object]:
    return {
        "answer": f"{text} [{evidence_id}]",
        "answer_type": "answer",
        "missing_facts": [],
        "claims": [{"claim_id": "C001", "text": text, "evidence_ids": [evidence_id]}],
    }


def verification(verdict: str, evidence_id: str, *, unanswered: list[str] | None = None) -> dict[str, object]:
    return {
        "verdict": verdict,
        "claim_checks": [{
            "claim_id": "C001",
            "status": "supported" if verdict == "pass" else "partially_supported",
            "evidence_ids": [evidence_id],
            "reason": "Проверено по фрагменту.",
        }],
        "unanswered_facts": unanswered or [],
        "issues": [] if verdict == "pass" else ["Нужен дополнительный источник."],
    }


def test_pipeline_exposes_final_verified_artifacts_with_stable_fragment_mapping() -> None:
    first = fragment("a.txt", "1", "Срок составляет пять дней.", 10)
    client = ScriptedClient([analysis(), generation("Срок составляет пять дней.", "E001"), verification("pass", "E001")])
    pipeline = SubmissionPipeline(SyntheticIndex([[first]]), client, top_k=4, evidence_k=2)

    rendered = pipeline.run("Каков срок?")

    artifacts = pipeline.last_artifacts
    assert "Срок составляет пять дней" in rendered
    assert artifacts["generation"]["claims"][0]["evidence_ids"] == ["E001"]
    assert artifacts["verification"]["verdict"] == "pass"
    assert artifacts["evidence_fragments"]["E001"] is first
    assert artifacts["stage_timings_ms"]["analysis"] >= 0


def test_revision_and_expanded_search_publish_only_final_artifacts() -> None:
    initial = fragment("a.txt", "1", "Общее условие.", 0)
    expanded = fragment("b.txt", "2", "Специальный срок — десять дней.", 50)
    client = ScriptedClient([
        analysis(),
        generation("Указано общее условие.", "E001"),
        verification("revision_required", "E001", unanswered=["специальный срок"]),
        generation("Специальный срок — десять дней.", "E002"),
        verification("pass", "E002"),
    ])
    pipeline = SubmissionPipeline(SyntheticIndex([[initial], [expanded]]), client, top_k=4, evidence_k=2)

    pipeline.run("Каков специальный срок?")

    artifacts = pipeline.last_artifacts
    assert artifacts["generation"]["claims"][0]["evidence_ids"] == ["E002"]
    assert artifacts["verification"]["verdict"] == "pass"
    assert artifacts["evidence_fragments"]["E002"] is expanded
    assert artifacts["outcome"]["verification"] == "pass"


def test_technical_failure_clears_previous_artifacts_before_retry() -> None:
    first = fragment("a.txt", "1", "Срок составляет пять дней.", 10)
    pipeline = SubmissionPipeline(
        SyntheticIndex([[first]]),
        ScriptedClient([analysis(), generation("Срок составляет пять дней.", "E001"), verification("pass", "E001")]),
        top_k=4,
        evidence_k=2,
    )
    pipeline.run("Каков срок?")
    assert pipeline.last_artifacts
    pipeline.client = ScriptedClient([TimeoutError("model timeout")])

    with pytest.raises(TimeoutError):
        pipeline.run("Повторите вопрос")

    assert pipeline.last_artifacts == {}
    assert pipeline.last_outcome["state"] == "technical_refusal"
