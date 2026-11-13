"""Безопасные технические метрики конкурсного запуска без текстов и секретов."""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


class MetricsRecorder:
    def __init__(self, configured_path: Path) -> None:
        self.details_path, self.summary_path = _resolve_paths(configured_path)
        self.details_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.details_path.write_text("", encoding="utf-8")
        self.started = perf_counter()
        self.started_at = datetime.now(UTC).isoformat()
        self.current_question_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.config: dict[str, Any] = {}

    def set_question(self, question_id: str | None) -> None:
        self.current_question_id = question_id

    def record_config(self, config: dict[str, Any]) -> None:
        self.config = config
        self.record("config", "run", **config)

    def record(self, kind: str, stage: str, **fields: Any) -> None:
        event: dict[str, Any] = {
            "kind": kind,
            "stage": stage,
            "question_id": self.current_question_id,
            **fields,
        }
        self.events.append(event)
        with self.details_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def finalize(self) -> dict[str, Any]:
        question_events = [event for event in self.events if event["kind"] == "question"]
        llm_events = [event for event in self.events if event["kind"] == "llm"]
        timed_events = [event for event in self.events if event.get("duration_ms") is not None]
        stages: dict[str, dict[str, Any]] = {}
        for event in timed_events:
            name = f"{event['kind']}.{event['stage']}"
            stages.setdefault(
                name,
                {"durations": [], "errors": 0, "requests": 0, "retries": 0},
            )
            stages[name]["durations"].append(float(event["duration_ms"]))
            stages[name]["errors"] += int(event.get("error_count", 0))
            stages[name]["requests"] += int(event.get("request_count", 0))
            stages[name]["retries"] += int(event.get("retry_count", 0))
        stage_summary = {
            name: {
                "count": len(data["durations"]),
                "errors": data["errors"],
                "requests": data["requests"],
                "retries": data["retries"],
                "p50_ms": _percentile(data["durations"], 0.50),
                "p95_ms": _percentile(data["durations"], 0.95),
            }
            for name, data in sorted(stages.items())
        }
        tokens = {
            name: _sum_nullable(event.get(name) for event in llm_events)
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        tokens["events_missing_usage"] = sum(
            1 for event in llm_events if event.get("total_tokens") is None
        )
        summary = {
            "config": self.config,
            "run": {
                "started_at": self.started_at,
                "duration_ms": round((perf_counter() - self.started) * 1000, 3),
                "questions_total": len(question_events),
                "questions_successful": sum(
                    1 for event in question_events if not event.get("technical_failure")
                ),
                "questions_failed": sum(
                    1 for event in question_events if event.get("technical_failure")
                ),
                "tokens": tokens,
            },
            "stages": stage_summary,
        }
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "metrics: "
            f"success={summary['run']['questions_successful']} "
            f"failed={summary['run']['questions_failed']} "
            f"tokens={tokens['total_tokens']} "
            f"summary={self.summary_path}",
            file=sys.stderr,
        )
        return summary


def _resolve_paths(path: Path) -> tuple[Path, Path]:
    if path.suffix.lower() == ".jsonl":
        return path, path.with_name(f"{path.stem}.summary.json")
    if path.suffix.lower() == ".json":
        return path.with_suffix(".jsonl"), path
    return path / "metrics.jsonl", path / "metrics-summary.json"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(interpolated, 3)


def _sum_nullable(values) -> int | None:
    present = [int(value) for value in values if value is not None]
    return sum(present) if present else None
