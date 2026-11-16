from __future__ import annotations

from submission.solution import RuntimeConfig, _supervisor_timeout


def test_competition_time_limit_remains_bounded(monkeypatch) -> None:
    monkeypatch.delenv("RAG_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("TIME_LIMIT_SECONDS", "7200")
    monkeypatch.setenv("SUPERVISOR_TIMEOUT_SECONDS", "7220")
    assert RuntimeConfig.from_env().time_limit_seconds == 1080.0
    assert _supervisor_timeout() == 1100.0


def test_dev_local_may_measure_longer_than_competition(monkeypatch) -> None:
    monkeypatch.setenv("RAG_RUNTIME_MODE", "DEV_LOCAL")
    monkeypatch.setenv("TIME_LIMIT_SECONDS", "7200")
    monkeypatch.setenv("SUPERVISOR_TIMEOUT_SECONDS", "7220")
    assert RuntimeConfig.from_env().time_limit_seconds == 7200.0
    assert _supervisor_timeout() == 7220.0


def test_top_k_is_read_directly_without_hidden_multiplier(monkeypatch) -> None:
    monkeypatch.delenv("RAG_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("TOP_K", "30")
    assert RuntimeConfig.from_env().top_k == 30


def test_competition_temperature_is_fixed_to_required_value(monkeypatch) -> None:
    monkeypatch.setenv("TEMPERATURE", "0.0")
    assert RuntimeConfig.from_env().temperature == 0.3
