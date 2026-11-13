"""Сбор технических и наблюдаемых качественных метрик с сравнением запусков."""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from threading import Lock, local
from typing import Any

QUALITY_FIELDS = {
    "quality.analysis": ("context_turns", "language_detected", "entities_count", "requested_facts_count", "lexical_queries_count", "semantic_queries_count", "ambiguous_count", "query_diversity"),
    "quality.generation": ("claims_count", "citations_count", "unique_citations_count", "claims_with_citations_rate", "answer_length", "language_match_heuristic"),
    "quality.verification": ("checked_claims_count", "verification_coverage", "self_reported_supported_rate", "unsupported_claims_count", "unanswered_facts_count", "issues_count", "passed"),
    "rag.bm25": ("candidates_count", "query_count"),
    "rag.faiss": ("candidates_count", "query_count"),
    "rag.fusion": ("candidates_count", "bm25_faiss_overlap", "unique_documents_count"),
    "rag.rerank": ("candidates_count", "selected_fragments_count", "unique_sources_count", "fallback_used", "score_available_rate", "top_score"),
    "rag.embeddings": ("query_count", "embedding_dimension", "vector_norm"),
    "quality.question": ("revised", "verified_pass", "answer_length"),
    "question.total": ("claims_count", "citations_count", "unsupported_claims_count", "revised", "verified_pass", "technical_failure", "answer_length"),
}


class MetricsRecorder:
    def __init__(self, configured_path: Path) -> None:
        self.details_path, self.summary_path = _resolve_paths(configured_path)
        self.details_path.parent.mkdir(parents=True, exist_ok=True)
        self.previous: dict[str, Any] | None = None
        if self.summary_path.is_file():
            try:
                previous = json.loads(self.summary_path.read_text(encoding="utf-8"))
                if isinstance(previous, dict):
                    self.previous = previous
            except (OSError, ValueError):
                pass
        self.details_path.write_text("", encoding="utf-8")
        self.started = perf_counter()
        self.started_at = datetime.now(UTC).isoformat()
        self._question_context = local()
        self._write_lock = Lock()
        self.events: list[dict[str, Any]] = []
        self.config: dict[str, Any] = {}

    def set_question(self, question_id: str | None) -> None:
        self._question_context.value = question_id

    def record_config(self, config: dict[str, Any]) -> None:
        self.config = config
        self.record("config", "run", **config)

    def record(self, kind: str, stage: str, **fields: Any) -> None:
        event: dict[str, Any] = {
            "kind": kind,
            "stage": stage,
            "question_id": getattr(self._question_context, "value", None),
            **fields,
        }
        with self._write_lock:
            self.events.append(event)
            with self.details_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")

    def finalize(self) -> dict[str, Any]:
        question_events = [e for e in self.events if e["kind"] == "question"]
        llm_events = [e for e in self.events if e["kind"] == "llm"]
        timed_events = [e for e in self.events if isinstance(e.get("duration_ms"), (int, float))]
        stage_data: dict[str, dict[str, Any]] = {}
        for event in timed_events:
            name = f"{event['kind']}.{event['stage']}"
            data = stage_data.setdefault(name, {"durations": [], "errors": 0, "requests": 0, "retries": 0})
            data["durations"].append(float(event["duration_ms"]))
            data["errors"] += int(event.get("error_count", 0))
            data["requests"] += int(event.get("request_count", 0))
            data["retries"] += int(event.get("retry_count", 0))
        stages = {
            name: {
                "count": len(d["durations"]),
                "errors": d["errors"],
                "requests": d["requests"],
                "retries": d["retries"],
                "p50_ms": _percentile(d["durations"], 0.5),
                "p95_ms": _percentile(d["durations"], 0.95),
            }
            for name, d in sorted(stage_data.items())
        }
        tokens = {
            name: _sum_nullable(event.get(name) for event in llm_events)
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        tokens["events_missing_usage"] = sum(1 for e in llm_events if e.get("total_tokens") is None)
        quality = self._quality_summary()
        budget_events = [e for e in self.events if e["kind"] == "budget" and e["stage"] == "run"]
        expected_questions = (
            int(budget_events[-1]["questions_total"])
            if budget_events and isinstance(budget_events[-1].get("questions_total"), int)
            else len(question_events)
        )
        successful_questions = sum(
            not bool(event.get("technical_failure")) for event in question_events
        )
        api_events = [event for event in self.events if event["kind"] == "api"]
        revision_events = [
            event
            for event in self.events
            if event["kind"] == "budget" and event["stage"] == "revision"
        ]
        budget_summary = {
            name: budget_events[-1].get(name)
            for name in (
                "limit_seconds",
                "elapsed_seconds",
                "remaining_seconds",
                "consumed_ratio",
            )
        } if budget_events else None
        previous_quality = self.previous.get("quality", {}) if self.previous else {}
        deltas: dict[str, dict[str, float]] = {}
        if isinstance(previous_quality, dict):
            for stage, items in quality.items():
                previous_stage = previous_quality.get(stage)
                if not isinstance(previous_stage, dict):
                    continue
                changes = {}
                for field, value in items.items():
                    old = previous_stage.get(field)
                    if field != "samples" and isinstance(value, (int, float)) and isinstance(old, (int, float)):
                        changes[field] = round(value - old, 5)
                if changes:
                    deltas[stage] = changes
        summary = {
            "config": self.config,
            "run": {
                "started_at": self.started_at,
                "duration_ms": round((perf_counter() - self.started) * 1000, 3),
                "questions_total": expected_questions,
                "questions_successful": successful_questions,
                "questions_failed": max(0, expected_questions - successful_questions),
                "api_calls": sum(int(event.get("request_count", 0)) for event in api_events),
                "api_errors": sum(int(event.get("error_count", 0)) for event in api_events),
                "api_retries": sum(int(event.get("retry_count", 0)) for event in api_events),
                "repeated_generations": sum(int(event.get("executed", 0)) for event in revision_events),
                "budget": budget_summary,
                "tokens": tokens,
            },
            "stages": stages,
            "quality": quality,
            "quality_deltas": deltas,
            "comparison_note": "Differences are observational; model-reported support is not independent legal accuracy.",
        }
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"metrics: success={summary['run']['questions_successful']} failed={summary['run']['questions_failed']} "
            f"tokens={tokens['total_tokens']} summary={self.summary_path}",
            file=sys.stderr,
        )
        for stage in ("quality.analysis", "rag.fusion", "rag.rerank", "quality.generation", "quality.verification", "quality.question", "question.total"):
            items = quality.get(stage)
            if not items:
                continue
            keys = [k for k in QUALITY_FIELDS[stage] if k in items and k not in {"answer_length", "entities_count"}]
            preview = []
            for key in keys[:4]:
                change = deltas.get(stage, {}).get(key)
                delta = f" ({change:+.3f})" if change is not None else ""
                preview.append(f"{key}={items[key]:.3f}{delta}")
            print(f"quality {stage}: " + " ".join(preview), file=sys.stderr)
        return summary

    def _quality_summary(self) -> dict[str, dict[str, float | int]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for event in self.events:
            name = f"{event['kind']}.{event['stage']}"
            if name in QUALITY_FIELDS:
                groups.setdefault(name, []).append(event)
        result: dict[str, dict[str, float | int]] = {}
        for stage, events in groups.items():
            row: dict[str, float | int] = {"samples": len(events)}
            for name in QUALITY_FIELDS[stage]:
                numbers = [float(e[name]) for e in events if isinstance(e.get(name), (int, float, bool))]
                if numbers:
                    row[name] = round(sum(numbers) / len(numbers), 5)
            result[stage] = row
        return result


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
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _sum_nullable(values) -> int | None:
    present = [int(v) for v in values if v is not None]
    return sum(present) if present else None
