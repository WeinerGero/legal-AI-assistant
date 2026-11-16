"""Local-only fourth-role DeepSeek judge; never imported by the competition ZIP."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from submission.model_client import ModelClient


JUDGE_SYSTEM = """You are an independent diagnostic judge for a closed-corpus legal RAG system. Return only JSON with case_id, criterion_scores, overall_score, critical_failure, explanation. Judge the supplied answer against the supplied reference and behavior. Do not invent facts."""


def smoke(path: Path) -> dict[str, Any]:
    client = ModelClient(max_tokens=64, temperature=0.0)
    base, key, model = client.base_url, client.api_key, client.llm_model
    started = __import__("time").perf_counter()
    result: dict[str, Any] = {"model": model}
    try:
        response = httpx.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": model, "temperature": 0.0, "max_tokens": 64, "messages": [{"role": "system", "content": "Return only JSON."}, {"role": "user", "content": "{\"ok\": true}"}]}, timeout=60.0)
        result.update({"http_status": response.status_code, "response": response.json(), "ok": response.is_success})
        result["response_sha256"] = hashlib.sha256(response.content).hexdigest()
    except Exception as exc:
        result.update({"ok": False, "error": type(exc).__name__})
    result["duration_ms"] = round((__import__("time").perf_counter() - started) * 1000, 3)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def judge_run(run_root: Path, cases: list[dict[str, Any]]) -> None:
    client_config = ModelClient(max_tokens=1200, temperature=0.0)
    base, key, model = client_config.base_url, client_config.api_key, client_config.llm_model
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
