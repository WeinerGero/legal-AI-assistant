from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from integration.app import create_app
from integration.handler import RagHandler


def _diagnostics(*, bge: bool, qwen: bool, deepseek: bool = True) -> dict[str, object]:
    return {
        "index": {"ready": True},
        "bge_m3": {"ready": bge},
        "qwen3": {"ready": qwen},
        "deepseek": {"ready": deepseek},
        "ready": bge and qwen and deepseek,
    }


def _handler(diagnostics: dict[str, object]) -> RagHandler:
    handler = RagHandler.__new__(RagHandler)
    handler.config = SimpleNamespace(
        readiness_probe_interval_seconds=30.0,
        readiness_retry_interval_seconds=0.01,
        readiness_probe_timeout_seconds=1.0,
    )
    handler._state_lock = threading.RLock()
    handler._probe_lock = threading.Lock()
    handler._probe_stop = threading.Event()
    handler.diagnostics = diagnostics
    handler.ready = bool(diagnostics["ready"])
    handler._next_probe_at = 0.0
    return handler


def test_initially_unavailable_models_recover_without_restarting_api() -> None:
    handler = _handler(_diagnostics(bge=False, qwen=False))
    models_ready = threading.Event()
    handler._probe_dependencies = lambda: _diagnostics(
        bge=models_ready.is_set(), qwen=models_ready.is_set()
    )
    handler._probe_thread = threading.Thread(target=handler._readiness_loop, daemon=True)
    handler._probe_thread.start()
    try:
        assert handler.readiness_snapshot()["ready"] is False
        models_ready.set()
        deadline = time.monotonic() + 1.0
        while not handler.readiness_snapshot()["ready"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert handler.readiness_snapshot()["ready"] is True
    finally:
        handler.close()


def test_dependency_failure_then_recovery_updates_snapshot() -> None:
    handler = _handler(_diagnostics(bge=True, qwen=True))
    available = True
    handler._probe_dependencies = lambda: _diagnostics(bge=available, qwen=available)
    handler._refresh_readiness_if_due()
    assert handler.readiness_snapshot()["ready"] is True

    available = False
    handler._next_probe_at = 0.0
    handler._refresh_readiness_if_due()
    assert handler.readiness_snapshot()["qwen3"]["ready"] is False

    available = True
    handler._next_probe_at = 0.0
    handler._refresh_readiness_if_due()
    assert handler.readiness_snapshot()["ready"] is True


def test_concurrent_readiness_checks_share_one_probe() -> None:
    handler = _handler(_diagnostics(bge=False, qwen=False))
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def probe() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1.0)
        return _diagnostics(bge=True, qwen=True)

    handler._probe_dependencies = probe
    with ThreadPoolExecutor(max_workers=8) as executor:
        future = executor.submit(handler.readiness_snapshot)
        assert started.wait(1.0)
        others = [executor.submit(handler.readiness_snapshot) for _ in range(7)]
        release.set()
        future.result()
        [item.result() for item in others]
    assert calls == 1


def test_readiness_probe_does_not_interrupt_active_request() -> None:
    handler = _handler(_diagnostics(bge=True, qwen=True))
    handler._model_lock = threading.BoundedSemaphore(1)
    calls = 0

    def probe() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _diagnostics(bge=True, qwen=True)

    handler._probe_dependencies = probe
    handler._model_lock.acquire()
    try:
        assert handler._refresh_readiness_if_due() is False
        assert calls == 0
    finally:
        handler._model_lock.release()
    assert handler._refresh_readiness_if_due() is True
    assert calls == 1


def test_ready_endpoints_distinguish_application_from_rag() -> None:
    from backend.app import app as backend_app

    original_lifespan = backend_app.router.lifespan_context
    app = create_app()
    handler = _handler(_diagnostics(bge=False, qwen=True))
    handler._next_probe_at = time.monotonic() + 60.0
    previous = getattr(app.state, "core_handler", None)
    app.state.core_handler = handler
    try:
        route = next(route for route in app.routes if route.path == "/integration/readyz")
        unavailable = route.endpoint()
        assert unavailable.status_code == 503
        assert json.loads(unavailable.body)["dependencies"]["bge_m3"]["ready"] is False

        handler.diagnostics = _diagnostics(bge=True, qwen=True)
        handler.ready = True
        available = route.endpoint()
        assert available.status_code == 200
        assert json.loads(available.body)["rag_ready"] is True
    finally:
        app.state.core_handler = previous
        app.router.lifespan_context = original_lifespan
