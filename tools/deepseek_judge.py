"""Local-only fourth-role DeepSeek judge; never imported by the competition ZIP."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

import httpx


JUDGE_SYSTEM = """You are an independent diagnostic judge for a closed-corpus legal RAG system. Return only JSON with case_id, criterion_scores, overall_score, critical_failure, explanation. Judge the supplied answer against the supplied reference and behavior. Do not invent facts."""


def judge_run(run_root: Path, cases: list[dict[str, Any]]) -> None:
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("LLM_DEV") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL", "deepseek-v4.1-flash")
    if not base or not key:
        raise RuntimeError("DeepSeek judge unavailable: LLM_BASE_URL or configured key is missing")
    answers = {}
    for path in run_root.glob("partitions/*/answers.csv"):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            answers.update({row["id"]: row.get("answer", "") for row in csv.DictReader(handle)})
    out = run_root / "judge_cases.jsonl"
    summary = {"evaluated": 0, "errors": 0, "model": model, "rounds": 1}
    with httpx.Client(timeout=120.0) as client, out.open("w", encoding="utf-8") as handle:
        for case in cases:
            case_id = str(case["id"])
            payload = {"case_id": case_id, "question": case["query"], "expected_behavior": case["expected_behavior"], "gold_answer": case["gold_answer"], "failure_conditions": case["failure_conditions"], "answer": answers.get(case_id, "")}
            try:
                response = client.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": model, "temperature": 0.0, "max_tokens": 1200, "messages": [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]})
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                result = json.loads(content) if isinstance(content, str) else content
                result["case_id"] = case_id
                summary["evaluated"] += 1
            except Exception as exc:
                result = {"case_id": case_id, "error": type(exc).__name__}
                summary["errors"] += 1
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    (run_root / "judge_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
