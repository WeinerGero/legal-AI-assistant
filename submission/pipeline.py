"""Самодостаточный конкурсный RAG-конвейер."""

from __future__ import annotations

import json
from time import perf_counter

try:  # пакет в репозитории / плоский корень конкурсного ZIP
    from submission.model_client import ModelClient
    from submission.search import HybridIndex
except ImportError:  # pragma: no cover
    from model_client import ModelClient
    from search import HybridIndex


ANALYSIS_PROMPT = """Ты анализатор юридических вопросов. Верни только JSON:
{"standalone_question":"...","entities":{},"requested_facts":[],"lexical_queries":[],"semantic_queries":[]}.
Не отвечай на вопрос и не выдумывай реквизиты."""

ANSWER_PROMPT = """Ты юридический ассистент. Ответь только по переданным доказательствам.
Верни JSON {"answer":"...","claims":[{"text":"...","evidence_ids":["E001"]}]}.
Каждое фактическое утверждение снабди ссылкой [E###]. Если данных недостаточно, явно откажись
от предположения и укажи, чего не хватает."""

VERIFY_PROMPT = """Ты независимый проверяющий. Сопоставь каждый тезис ответа с доказательствами,
проверь даты, источники и корректность отказа. Верни только JSON:
{"verdict":"pass|revision_required|insufficient_evidence","issues":[],"corrected_answer":"..."}.
corrected_answer допустим только как удаление или исправление неподтвержденных тезисов."""


class SubmissionPipeline:
    def __init__(self, index: HybridIndex, client: ModelClient, *, top_k: int = 30, evidence_k: int = 10) -> None:
        self.index = index
        self.client = client
        self.top_k = top_k
        self.evidence_k = evidence_k

    def run(self, question: str) -> str:
        started = perf_counter()
        try:
            analysis = self._json(
                self.client.complete_json(ANALYSIS_PROMPT, question, stage="analysis")
            )
            standalone = str(analysis.get("standalone_question") or question)
            queries = analysis.get("lexical_queries", []) + analysis.get("semantic_queries", [])
            query = " ".join(str(item) for item in queries if str(item).strip()) or standalone
            fragments = self.index.search(query, top_k=self.top_k, final_k=self.evidence_k)
            evidence = [
                {
                    "evidence_id": f"E{number:03d}",
                    "source": item.source,
                    "section": item.section,
                    "dates": item.dates,
                    "relations": item.relations,
                    "text": item.text,
                }
                for number, item in enumerate(fragments, 1)
            ]
            generation_payload = json.dumps(
                {
                    "question": standalone,
                    "requested_facts": analysis.get("requested_facts", []),
                    "evidence": evidence,
                },
                ensure_ascii=False,
            )
            generation = self._json(
                self.client.complete_json(
                    ANSWER_PROMPT, generation_payload, stage="generation"
                )
            )
            answer = str(generation.get("answer") or "").strip()
            verification = self._json(
                self.client.complete_json(
                    VERIFY_PROMPT,
                    json.dumps(
                        {
                            "question": standalone,
                            "answer": answer,
                            "claims": generation.get("claims", []),
                            "evidence": evidence,
                        },
                        ensure_ascii=False,
                    ),
                    stage="verification",
                )
            )
            if verification.get("verdict") != "pass":
                corrected = str(verification.get("corrected_answer") or "").strip()
                if corrected:
                    answer = corrected
                else:
                    answer = "Недостаточно подтверждённых данных в предоставленных документах для надёжного ответа."
            result = answer or "Недостаточно данных для ответа."
            self._record_question(started, generation, verification, technical_failure=False)
            return result
        except Exception as exc:
            self._record_question(
                started,
                None,
                None,
                technical_failure=True,
                error=exc,
            )
            raise

    def _record_question(
        self,
        started: float,
        generation: dict | None,
        verification: dict | None,
        *,
        technical_failure: bool,
        error: Exception | None = None,
    ) -> None:
        metrics = self.client.metrics
        if metrics is None:
            return
        claims = generation.get("claims", []) if generation else []
        claim_rows = claims if isinstance(claims, list) else []
        citations = sum(
            len(claim.get("evidence_ids", []))
            for claim in claim_rows
            if isinstance(claim, dict) and isinstance(claim.get("evidence_ids", []), list)
        )
        checks = verification.get("claim_checks") if verification else None
        if isinstance(checks, list):
            unsupported: int | None = sum(
                1
                for check in checks
                if isinstance(check, dict)
                and check.get("status") in {"unsupported", "contradicted"}
            )
        elif verification and verification.get("verdict") == "pass":
            unsupported = 0
        else:
            unsupported = None
        metrics.record(
            "question",
            "total",
            duration_ms=round((perf_counter() - started) * 1000, 3),
            verification_result=verification.get("verdict") if verification else None,
            claims_count=len(claim_rows),
            citations_count=citations,
            unsupported_claims_count=unsupported,
            technical_failure=technical_failure,
            error_count=int(technical_failure),
            error_type=type(error).__name__ if error is not None else None,
        )

    @staticmethod
    def _json(value: str) -> dict:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("модель вернула JSON не в виде объекта")
        return data
