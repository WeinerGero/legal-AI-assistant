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
from integration.handler import ModelDependencyError, RagHandler  # noqa: E402
from backend.app import _history_result  # noqa: E402
from backend.core_gateway import CoreUnavailable  # noqa: E402
from integration.routing import RouteDecision, parse_route  # noqa: E402
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
    handler.route_question = lambda question, history: RouteDecision(
        route="legal", answer_style="standard", action=None, history_target=None, reply=None
    )
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
        "answer_style": "detailed",
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
    assert submitted["answer_style"] == "detailed"
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


def test_model_queue_timeout_becomes_technical_failure() -> None:
    handler, _pipeline = _handler()
    handler.config = SimpleNamespace(request_timeout_seconds=0.01)
    request = ModelQuestion.model_validate({
        "schema_version": "1.0",
        "request_id": "queue-timeout",
        "question": "Когда наступает срок оплаты?",
        "context": {},
        "recent_messages": [],
        "summary": "",
        "corpus_id": "corpus-test",
        "index_generation_id": "generation-test",
        "context_revision": 0,
    })

    handler._model_lock.acquire()
    started = time.perf_counter()
    try:
        with pytest.raises(ModelDependencyError, match="rag: request deadline exceeded"):
            handler(request.model_dump_json())
    finally:
        handler._model_lock.release()

    assert time.perf_counter() - started < 1.0


def test_history_question_uses_full_dialogue_without_retrieval() -> None:
    messages = [SimpleNamespace(role="user", content="Первый вопрос")]
    for number in range(30):
        messages.extend([
            SimpleNamespace(role="assistant", content=f"Ответ {number}"),
            SimpleNamespace(role="user", content=f"Вопрос {number}"),
        ])
    result = _history_result("first_user", messages)
    assert result is not None
    assert result.answer.blocks[0].segments[0].text == "Первый вопрос"
    assert result.answer.retrieval_audit.performed is False


def test_history_question_skips_earlier_history_requests() -> None:
    messages = [
        SimpleNamespace(role="user", content="Какой срок оплаты по договору А?"),
        SimpleNamespace(role="assistant", content="Срок подтверждён источником."),
        SimpleNamespace(role="user", content="что я спросил?"),
        SimpleNamespace(role="assistant", content="Какой срок оплаты по договору А?", answer_json={"retrieval_audit": {"performed": False, "passes": ["conversation_history"]}}),
    ]
    result = _history_result("last_user", messages)
    assert result is not None
    assert result.answer.blocks[0].segments[0].text == "Какой срок оплаты по договору А?"


def test_greeting_does_not_require_rag_but_legal_question_does() -> None:
    from fastapi.testclient import TestClient
    from backend.app import app, lifespan

    class FakeRouter:
        config = SimpleNamespace(request_timeout_seconds=330.0)

        def route_question(self, question: str, history: list[dict[str, str]]) -> RouteDecision:
            replies = {"ривет": "Здравствуйте!", "пока": "До свидания!", "хай": "Привет!"}
            if question in replies:
                return RouteDecision(route="conversation", answer_style="standard", action="reply", history_target=None, reply=replies[question])
            return RouteDecision(route="legal", answer_style="brief", action=None, history_target=None, reply=None)

        def __call__(self, payload: str) -> CoreResult:
            raise CoreUnavailable("RAG is unavailable")

    assert parse_route('{"route":"conversation","answer_style":"standard","action":"reply","history_target":null,"reply":"До свидания!"}').reply == "До свидания!"
    previous_handler = app.state.core_handler
    previous_lifespan = app.router.lifespan_context
    app.router.lifespan_context = lifespan
    app.state.core_handler = FakeRouter()
    try:
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"login": "tester", "password": "test-password"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            conversation = client.post("/api/v1/conversations", json={}, headers=headers)
            conversation_id = conversation.json()["id"]
            endpoint = f"/api/v1/conversations/{conversation_id}/messages"
            for phrase in ("ривет", "пока", "хай"):
                greeting = client.post(endpoint, json={"content": phrase}, headers=headers)
                assert greeting.status_code == 201
                assert greeting.json()["retrieval_audit"] == {"performed": False, "passes": ["conversation_reply"]}
                assert greeting.json()["sources"] == []
            legal = client.post(endpoint, json={"content": "Какой срок оплаты?"}, headers=headers)
            assert legal.status_code == 503
    finally:
        app.state.core_handler = previous_handler
        app.router.lifespan_context = previous_lifespan


def test_router_history_uses_saved_question_without_second_retrieval() -> None:
    from fastapi.testclient import TestClient
    from backend.app import app, lifespan

    handler, pipeline = _handler()
    handler.route_question = lambda question, history: RouteDecision(
        route="conversation" if question == "Что я спросил?" else "legal",
        answer_style="standard",
        action="history" if question == "Что я спросил?" else None,
        history_target="last_user" if question == "Что я спросил?" else None,
        reply=None,
    )
    previous_handler = app.state.core_handler
    previous_lifespan = app.router.lifespan_context
    app.router.lifespan_context = lifespan
    app.state.core_handler = handler
    try:
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"login": "tester", "password": "test-password"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            conversation = client.post("/api/v1/conversations", json={}, headers=headers)
            endpoint = f"/api/v1/conversations/{conversation.json()['id']}/messages"
            question = "С кем нужно согласовывать договоры по регламенту?"
            assert client.post(endpoint, json={"content": question}, headers=headers).status_code == 201
            first_input = pipeline.input
            answer = client.post(endpoint, json={"content": "Что я спросил?"}, headers=headers)
            assert answer.status_code == 201
            assert answer.json()["blocks"][0]["segments"][0]["text"] == question
            assert answer.json()["retrieval_audit"]["performed"] is False
            assert pipeline.input == first_input
    finally:
        app.state.core_handler = previous_handler
        app.router.lifespan_context = previous_lifespan


def test_router_receives_current_dialogue_and_user_corrections_after_reload() -> None:
    from fastapi.testclient import TestClient
    from backend.app import app, lifespan

    class FakeRouter:
        config = SimpleNamespace(request_timeout_seconds=330.0)

        def route_question(self, question: str, history: list[dict[str, str]]) -> RouteDecision:
            if question == "Сколько мне лет?":
                facts = [item["content"] for item in history if item["role"] == "user" and item["content"].startswith("Мне ")]
                reply = facts[-1].replace("Мне ", "Вам ") if facts else "Возраст не указан."
            else:
                reply = "Понял."
            return RouteDecision(route="conversation", answer_style="standard", action="reply", history_target=None, reply=reply)

    previous_handler = app.state.core_handler
    previous_lifespan = app.router.lifespan_context
    app.router.lifespan_context = lifespan
    app.state.core_handler = FakeRouter()
    try:
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"login": "tester", "password": "test-password"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            first = client.post("/api/v1/conversations", json={}, headers=headers).json()["id"]
            second = client.post("/api/v1/conversations", json={}, headers=headers).json()["id"]

            def ask(conversation_id: str, content: str) -> str:
                response = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": content}, headers=headers,
                )
                assert response.status_code == 201
                return response.json()["blocks"][0]["segments"][0]["text"]

            ask(first, "Мне 23 года.")
            assert ask(first, "Сколько мне лет?") == "Вам 23 года."
            ask(first, "Мне 24 года.")
            assert ask(first, "Сколько мне лет?") == "Вам 24 года."
            ask(second, "Мне 41 год.")
            assert ask(second, "Сколько мне лет?") == "Вам 41 год."
            assert len(client.get(f"/api/v1/conversations/{first}/messages", headers=headers).json()) == 8
    finally:
        app.state.core_handler = previous_handler
        app.router.lifespan_context = previous_lifespan


def test_model_router_receives_entire_prior_history_without_current_question() -> None:
    class RouterClient:
        budget = None

        def complete_json(self, prompt: str, payload: str, *, stage: str) -> str:
            self.payload = json.loads(payload)
            return json.dumps({
                "route": "conversation", "answer_style": "standard", "action": "reply",
                "history_target": None, "reply": "Вам 23 года.",
            }, ensure_ascii=False)

    handler = RagHandler.__new__(RagHandler)
    handler.router_client = RouterClient()
    handler._router_lock = threading.Lock()
    history = [{"role": "user", "content": f"Сообщение {number}"} for number in range(32)]
    history.append({"role": "user", "content": "Мне 23 года."})
    decision = handler.route_question("Сколько мне лет?", history)
    assert decision.reply == "Вам 23 года."
    assert handler.router_client.payload["history"] == history
    assert all(item["content"] != "Сколько мне лет?" for item in handler.router_client.payload["history"])


def test_detailed_verified_presentation_has_conclusion_and_explanation() -> None:
    handler, pipeline = _handler()
    artifacts = pipeline.last_artifacts
    artifacts["generation"]["claims"].append({
        "claim_id": "C002", "text": "Срок начинается с указанного события.", "evidence_ids": ["E001"],
    })
    artifacts["verification"]["claim_checks"].append({
        "claim_id": "C002", "status": "supported", "evidence_ids": ["E001"], "reason": "Подтверждено фрагментом.",
    })
    artifacts["verification"]["presentation_blocks"] = [{
        "block_type": "conclusion",
        "segments": [
            {"text": "Оплата производится в течение пяти дней.", "claim_ids": ["C001"], "evidence_ids": ["E001"]},
            {"text": "Срок начинается с указанного события.", "claim_ids": ["C002"], "evidence_ids": ["E001"]},
        ],
    }]
    request = ModelQuestion.model_validate({
        "schema_version": "1.0", "request_id": "detailed-layout", "question": "Объясни подробно срок оплаты",
        "context": {}, "recent_messages": [], "summary": None,
        "corpus_id": "corpus-test", "index_generation_id": "generation-test", "context_revision": 0,
        "answer_style": "detailed",
    })
    result = handler._to_core_result(request, artifacts)
    assert [block.block_type for block in result.answer.blocks] == ["conclusion", "justification"]
    assert len(result.answer.sources) == 1
