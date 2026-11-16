from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

TEST_DATABASE = Path("test_backend_rag_integration.db").resolve()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["APP_USER_LOGIN"] = "tester"
os.environ["APP_USER_PASSWORD"] = "test-password"
os.environ["CORPUS_ID"] = "corpus-test"
os.environ["INDEX_GENERATION_ID"] = "generation-test"

from backend.schemas import CoreResult, ModelQuestion  # noqa: E402
from integration.app import create_app  # noqa: E402
from integration.config import IntegrationConfig  # noqa: E402
from integration.handler import RagHandler  # noqa: E402
from submission.search import Fragment  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def clean_test_database() -> None:
    yield
    TEST_DATABASE.unlink(missing_ok=True)


class FakeClient:
    budget = None

    def reset_question_trace(self) -> None:
        pass

    def question_trace(self) -> dict[str, object]:
        return {"stage": "verification"}


class FakePipeline:
    def __init__(self, fragment: Fragment) -> None:
        self.input = ""
        self.last_artifacts = {
            "analysis": {"standalone_question": "Когда платить?"},
            "evidence": [{"evidence_id": "E001", "section": "1.1", "text": fragment.text}],
            "fragments": [fragment],
            "evidence_fragments": {"E001": fragment},
            "generation": {
                "answer": "Оплата производится в течение пяти дней [E001]",
                "answer_type": "answer",
                "missing_facts": [],
                "claims": [{
                    "claim_id": "C001",
                    "text": "Оплата производится в течение пяти дней.",
                    "evidence_ids": ["E001"],
                }],
            },
            "verification": {
                "verdict": "pass",
                "claim_checks": [{
                    "claim_id": "C001",
                    "status": "supported",
                    "evidence_ids": ["E001"],
                    "reason": "Совпадает с пунктом.",
                }],
                "unanswered_facts": [],
            },
            "rendered_text": "Оплата производится в течение пяти дней [1]",
        }

    def run(self, value: str) -> str:
        self.input = value
        return "Оплата производится в течение пяти дней [1]"


def _handler() -> tuple[RagHandler, FakePipeline]:
    canonical = "Преамбула.\n1.1. Оплата производится в течение пяти дней.\nКонец."
    text = "1.1. Оплата производится в течение пяти дней."
    start = canonical.index(text)
    fragment = Fragment(
        source="contract.txt",
        section="1.1",
        text=text,
        dates=(),
        relations=(),
        start_char=start,
        end_char=start + len(text),
        citation_section="1.1",
    )
    pipeline = FakePipeline(fragment)
    handler = RagHandler.__new__(RagHandler)
    handler.config = SimpleNamespace(request_timeout_seconds=10.0)
    handler.client = FakeClient()
    handler.pipeline = pipeline
    handler.documents = {"contract.txt": canonical}
    handler._slots = threading.BoundedSemaphore(2)
    handler._model_lock = threading.BoundedSemaphore(1)
    handler.ready = True
    return handler, pipeline


def test_handler_returns_strict_core_result_with_exact_source_span() -> None:
    handler, pipeline = _handler()
    request = ModelQuestion.model_validate({
        "schema_version": "1.0",
        "request_id": "req-test",
        "question": "А когда платить?",
        "context": {
            "active_document": {"contract_number": "A-1", "counterparty": "Контрагент"},
            "event_date": "2025-01-15",
        },
        "recent_messages": [{"role": "user", "content": "Речь о договоре A-1"}],
        "summary": "Обсуждается оплата",
        "corpus_id": "corpus-test",
        "index_generation_id": "generation-test",
        "context_revision": 2,
    })

    result = handler(request.model_dump_json())

    assert isinstance(result, CoreResult)
    assert result.answer.legal_status == "answered"
    assert result.answer.sources[0].answer_source_id == result.source_locations[0].answer_source_id
    location = result.source_locations[0]
    assert location.canonical_text[location.start_char:location.end_char] == (
        "1.1. Оплата производится в течение пяти дней."
    )
    submitted = json.loads(pipeline.input)
    assert submitted["messages"][0]["content"] == "Речь о договоре A-1"
    assert "2025-01-15" in submitted["messages"][-1]["content"]
    assert "Обсуждается оплата" in submitted["messages"][-1]["content"]


def test_backend_persists_and_opens_exact_source(request: pytest.FixtureRequest) -> None:
    from fastapi.testclient import TestClient
    from backend.app import app

    handler, pipeline = _handler()
    app.state.core_handler = handler
    request.addfinalizer(lambda: setattr(app.state, "core_handler", None))
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"login": "tester", "password": "test-password"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        conversation = client.post(
            "/api/v1/conversations", json={"title": "Интеграционный тест"}, headers=headers
        )
        assert conversation.status_code == 201
        conversation_id = conversation.json()["id"]
        answer_response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={
                "content": "Когда платить?",
                "context": {"active_document": {"contract_number": "A-1"}, "event_date": "2025-01-15"},
            },
            headers=headers,
        )
        assert answer_response.status_code == 201, answer_response.text
        answer = answer_response.json()
        source = answer["sources"][0]
        snippet = client.get(
            f"/api/v1/answers/{answer['answer_id']}/sources/{source['answer_source_id']}",
            headers=headers,
        )
        assert snippet.status_code == 200
        assert snippet.json()["highlight"] == "1.1. Оплата производится в течение пяти дней."
        assert client.get(
            f"/api/v1/answers/{answer['answer_id']}/sources/not-this-source", headers=headers
        ).status_code == 404
        messages = client.get(
            f"/api/v1/conversations/{conversation_id}/messages", headers=headers
        ).json()
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert "2025-01-15" in json.loads(pipeline.input)["messages"][-1]["content"]
    app.state.core_handler = None


def test_factory_preserves_backend_routes_without_import_side_effect_handler() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/conversations/{conversation_id}/messages" in paths
    assert "/api/v1/answers/{answer_id}/sources/{answer_source_id}" in paths
    assert "/integration/readyz" in paths
    assert app.state.core_handler is None


def test_invalid_or_missing_evidence_mapping_is_rejected() -> None:
    handler, pipeline = _handler()
    request = ModelQuestion.model_validate({
        "schema_version": "1.0",
        "request_id": "invalid-map",
        "question": "Когда платить?",
        "context": {},
        "recent_messages": [],
        "summary": "",
        "corpus_id": "corpus-test",
        "index_generation_id": "generation-test",
        "context_revision": 0,
    })
    artifacts = dict(pipeline.last_artifacts)
    artifacts["evidence_fragments"] = {}

    try:
        result = handler._to_core_result(request, artifacts)
    except ValueError:
        pass
    else:
        assert result.answer.sources == []
        assert result.answer.legal_status == "insufficient_evidence"


def test_parallel_requests_keep_their_own_artifact_snapshot() -> None:
    first_handler, first_pipeline = _handler()
    first_fragment = first_pipeline.last_artifacts["evidence_fragments"]["E001"]
    second_fragment = Fragment(
        source="other.txt",
        section="2.1",
        text="2.1. Другой проверенный срок составляет десять дней.",
        dates=(),
        relations=(),
        start_char=0,
        end_char=len("2.1. Другой проверенный срок составляет десять дней."),
        citation_section="2.1",
    )
    first_handler.documents["other.txt"] = second_fragment.text

    class ConcurrentPipeline:
        last_artifacts: dict[str, object] = {}

        def run(self, raw: str) -> str:
            payload = json.loads(raw)
            use_second = "другой" in payload["messages"][-1]["content"].casefold()
            selected = second_fragment if use_second else first_fragment
            text = "Срок составляет десять дней." if use_second else "Срок составляет пять дней."
            time.sleep(0.02)
            self.last_artifacts = {
                "generation": {"answer": text, "answer_type": "answer", "missing_facts": [], "claims": [{"claim_id": "C001", "text": text, "evidence_ids": ["E001"]}]},
                "verification": {"verdict": "pass", "claim_checks": [{"claim_id": "C001", "status": "supported", "evidence_ids": ["E001"], "reason": "Проверено."}], "unanswered_facts": []},
                "evidence": [{"evidence_id": "E001", "section": selected.section, "text": selected.text}],
                "fragments": [selected],
                "evidence_fragments": {"E001": selected},
                "stage_timings_ms": {"test": 20.0},
            }
            return text

    first_handler.pipeline = ConcurrentPipeline()

    def payload(request_id: str, question: str) -> str:
        return ModelQuestion.model_validate({
            "schema_version": "1.0", "request_id": request_id, "question": question,
            "context": {}, "recent_messages": [], "summary": "", "corpus_id": "corpus-test",
            "index_generation_id": "generation-test", "context_revision": 0,
        }).model_dump_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda args: first_handler(payload(*args)), [("one", "Срок?"), ("two", "Другой срок?")]))

    assert {result.answer.sources[0].document_title for result in results} == {"contract", "other"}


def test_request_budget_must_finish_before_nginx_timeout(tmp_path) -> None:
    with pytest.raises(ValueError, match="between 0 and 360"):
        IntegrationConfig(tmp_path / "corpus.zip", tmp_path, request_timeout_seconds=360)
