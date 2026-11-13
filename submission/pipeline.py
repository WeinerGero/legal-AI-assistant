"""Самодостаточный конкурсный RAG-конвейер."""

from __future__ import annotations

import json

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
        analysis = self._json(self.client.complete_json(ANALYSIS_PROMPT, question))
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
            {"question": standalone, "requested_facts": analysis.get("requested_facts", []), "evidence": evidence},
            ensure_ascii=False,
        )
        generation = self._json(self.client.complete_json(ANSWER_PROMPT, generation_payload))
        answer = str(generation.get("answer") or "").strip()
        verification = self._json(
            self.client.complete_json(
                VERIFY_PROMPT,
                json.dumps(
                    {"question": standalone, "answer": answer, "claims": generation.get("claims", []), "evidence": evidence},
                    ensure_ascii=False,
                ),
            )
        )
        if verification.get("verdict") != "pass":
            corrected = str(verification.get("corrected_answer") or "").strip()
            if corrected:
                answer = corrected
            else:
                answer = "Недостаточно подтверждённых данных в предоставленных документах для надёжного ответа."
        return answer or "Недостаточно данных для ответа."

    @staticmethod
    def _json(value: str) -> dict:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("модель вернула JSON не в виде объекта")
        return data
