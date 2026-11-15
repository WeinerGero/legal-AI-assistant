from __future__ import annotations

from submission.solution import RuntimeConfig, _supervisor_timeout


def test_competition_time_limit_remains_bounded(monkeypatch) -> None:
    monkeypatch.delenv("RAG_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("TIME_LIMIT_SECONDS", "3600")
    monkeypatch.setenv("SUPERVISOR_TIMEOUT_SECONDS", "3620")
    assert RuntimeConfig.from_env().time_limit_seconds == 1080.0
    assert _supervisor_timeout() == 1100.0


def test_dev_local_may_measure_longer_than_competition(monkeypatch) -> None:
    monkeypatch.setenv("RAG_RUNTIME_MODE", "DEV_LOCAL")
    monkeypatch.setenv("TIME_LIMIT_SECONDS", "3600")
    monkeypatch.setenv("SUPERVISOR_TIMEOUT_SECONDS", "3620")
    assert RuntimeConfig.from_env().time_limit_seconds == 3600.0
    assert _supervisor_timeout() == 3620.0
