"""Самодостаточный конкурсный RAG-конвейер с claim-level проверкой."""

from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter

try:
    from submission.model_client import ModelClient
    from submission.search import HybridIndex
except ImportError:  # плоский корень submission.zip
    from model_client import ModelClient
    from search import HybridIndex

CLAIM_ID = re.compile(r"^C\d{3,}$")
EVIDENCE_ID = re.compile(r"^E\d{3,}$")
INLINE_EVIDENCE = re.compile(r"\[(E\d{3,})\]")
LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
ROLE_LINE = re.compile(r"(?im)^(user|assistant|пользователь|ассистент)\s*:\s*")
CLAIM_STATUSES = {"supported", "partially_supported", "unsupported", "contradicted"}
VERDICTS = {"pass", "revision_required", "insufficient_evidence"}


class SubmissionPipeline:
    def __init__(self, index: HybridIndex, client: ModelClient, *, top_k: int = 30, evidence_k: int = 10) -> None:
        self.index = index
        self.client = client
        self.top_k = top_k
        self.evidence_k = evidence_k
        self.analysis_prompt = _load_prompt("analysis_v2.txt")
        self.generation_prompt = _load_prompt("generation_v2.txt")
        self.verification_prompt = _load_prompt("verification_v2.txt")

    def run(self, question: str) -> str:
        started = perf_counter()
        try:
            messages = parse_conversation(question)
            analysis = self._json(
                self.client.complete_json(
                    self.analysis_prompt,
                    json.dumps({"messages": messages}, ensure_ascii=False),
                    stage="analysis",
                )
            )
            _validate_analysis(analysis)
            standalone = analysis["standalone_question"].strip()
            queries = analysis["lexical_queries"] + analysis["semantic_queries"]
            query = " ".join(str(item) for item in queries if str(item).strip()) or standalone
            fragments = self.index.search(query, top_k=self.top_k, final_k=self.evidence_k)
            evidence = [
                {
                    "evidence_id": f"E{number:03d}",
                    "document": item.source,
                    "section": self.index.verified_citation_section(item),
                    "dates": item.dates,
                    "relations": item.relations,
                    "text": item.text,
                }
                for number, item in enumerate(fragments, 1)
            ]
            self.client.recording.checkpoint("evidence.order", evidence)
            generation_request = {
                "mode": "initial",
                "target_language": analysis["target_language"],
                "question": standalone,
                "requested_facts": analysis["requested_facts"],
                "evidence": evidence,
            }
            generation = self._generate(generation_request, evidence)
            verification = self._verify(messages, analysis, generation, evidence)
            if verification["verdict"] != "pass":
                generation = self._generate(
                    {
                        **generation_request,
                        "mode": "revision",
                        "previous_generation": generation,
                        "verification": verification,
                    },
                    evidence,
                )
                verification = self._verify(messages, analysis, generation, evidence)
            if verification["verdict"] == "pass":
                result = _render_citations(
                    generation, evidence, target_language=analysis["target_language"]
                )
            else:
                result = _insufficient_evidence_answer(analysis["target_language"])
            self._record_question(started, generation, verification, technical_failure=False)
            return result
        except Exception as exc:
            self._record_question(started, None, None, technical_failure=True, error=exc)
            raise

    def _generate(self, request: dict, evidence: list[dict]) -> dict:
        generation = self._json(
            self.client.complete_json(
                self.generation_prompt,
                json.dumps(request, ensure_ascii=False),
                stage="generation",
            )
        )
        _validate_generation(generation, evidence)
        return generation

    def _verify(
        self,
        messages: list[dict[str, str]],
        analysis: dict,
        generation: dict,
        evidence: list[dict],
    ) -> dict:
        verification = self._json(
            self.client.complete_json(
                self.verification_prompt,
                json.dumps(
                    {
                        "messages": messages,
                        "target_language": analysis["target_language"],
                        "standalone_question": analysis["standalone_question"],
                        "requested_facts": analysis["requested_facts"],
                        "generation": generation,
                        "evidence": evidence,
                    },
                    ensure_ascii=False,
                ),
                stage="verification",
            )
        )
        _validate_verification(verification, generation, evidence)
        return verification

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
        unsupported = (
            sum(
                1
                for check in checks
                if isinstance(check, dict)
                and check.get("status") in {"unsupported", "contradicted"}
            )
            if isinstance(checks, list)
            else None
        )
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


def parse_conversation(raw: str) -> list[dict[str, str]]:
    """Извлекает роли из JSON или размеченного текста; строка CSV изолирована."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    candidate = parsed.get("messages") if isinstance(parsed, dict) else parsed
    if isinstance(candidate, list):
        messages = [
            {"role": item["role"], "content": item["content"].strip()}
            for item in candidate
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ]
        if messages and any(item["role"] == "user" for item in messages):
            return messages
    matches = list(ROLE_LINE.finditer(raw))
    if matches:
        role_map = {
            "user": "user",
            "пользователь": "user",
            "assistant": "assistant",
            "ассистент": "assistant",
        }
        messages = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            content = raw[match.end() : end].strip()
            if content:
                messages.append({"role": role_map[match.group(1).lower()], "content": content})
        if messages and any(item["role"] == "user" for item in messages):
            return messages
    return [{"role": "user", "content": raw.strip()}]


def _validate_analysis(analysis: dict) -> None:
    language = analysis.get("target_language")
    if not isinstance(language, str) or not LANGUAGE_TAG.fullmatch(language):
        raise ValueError("analysis.target_language не является BCP-47 tag")
    if not isinstance(analysis.get("standalone_question"), str) or not analysis[
        "standalone_question"
    ].strip():
        raise ValueError("analysis.standalone_question пуст")
    if not isinstance(analysis.get("entities"), dict):
        raise ValueError("analysis.entities должен быть объектом")
    for name in (
        "requested_facts",
        "lexical_queries",
        "semantic_queries",
        "unresolved_ambiguities",
    ):
        if not isinstance(analysis.get(name), list):
            raise ValueError(f"analysis.{name} должен быть массивом")


def _validate_generation(generation: dict, evidence: list[dict]) -> None:
    if not isinstance(generation.get("answer"), str) or not generation["answer"].strip():
        raise ValueError("generation.answer пуст")
    if not isinstance(generation.get("missing_facts", []), list):
        raise ValueError("generation.missing_facts должен быть массивом")
    claims = generation.get("claims")
    if not isinstance(claims, list):
        raise ValueError("generation.claims должен быть массивом")
    if not claims:
        raise ValueError("публикация ответа без structured claims запрещена")
    known = {item["evidence_id"] for item in evidence}
    seen: set[str] = set()
    answer_ids = set(INLINE_EVIDENCE.findall(generation["answer"]))
    if not answer_ids <= known:
        raise ValueError(f"answer содержит неизвестные evidence IDs: {sorted(answer_ids - known)}")
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claim должен быть объектом")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not CLAIM_ID.fullmatch(claim_id) or claim_id in seen:
            raise ValueError(f"некорректный или повторный claim_id: {claim_id}")
        seen.add(claim_id)
        if not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError(f"пустой текст claim: {claim_id}")
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError(f"claim без evidence: {claim_id}")
        if any(
            not isinstance(item, str) or not EVIDENCE_ID.fullmatch(item)
            for item in evidence_ids
        ):
            raise ValueError(f"некорректный evidence ID в claim: {claim_id}")
        unknown = set(evidence_ids) - known
        if unknown:
            raise ValueError(f"claim {claim_id} содержит неизвестные evidence IDs: {sorted(unknown)}")
        missing_inline = set(evidence_ids) - answer_ids
        if missing_inline:
            raise ValueError(f"claim {claim_id} не связан с inline evidence: {sorted(missing_inline)}")
    claim_ids = {
        evidence_id
        for claim in claims
        for evidence_id in claim["evidence_ids"]
    }
    if answer_ids != claim_ids:
        raise ValueError("набор ссылок answer не совпадает со structured claims")
    raw_ids = set(re.findall(r"\bE\d{3,}\b", generation["answer"]))
    if raw_ids != answer_ids:
        raise ValueError("answer содержит E### вне формата [E###]")


def _validate_verification(verification: dict, generation: dict, evidence: list[dict]) -> None:
    verdict = verification.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"неизвестный verification verdict: {verdict}")
    checks = verification.get("claim_checks")
    if not isinstance(checks, list):
        raise ValueError("verification.claim_checks должен быть массивом")
    known_evidence = {item["evidence_id"] for item in evidence}
    claim_ids = {item["claim_id"] for item in generation["claims"]}
    claim_evidence = {
        item["claim_id"]: set(item["evidence_ids"]) for item in generation["claims"]
    }
    checked: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("claim_id") not in claim_ids:
            raise ValueError("verification содержит неизвестный claim")
        claim_id = check["claim_id"]
        if claim_id in checked or check.get("status") not in CLAIM_STATUSES:
            raise ValueError(f"некорректная повторная проверка claim: {claim_id}")
        checked.add(claim_id)
        evidence_ids = check.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not set(evidence_ids) <= known_evidence:
            raise ValueError(f"verification содержит неизвестные evidence IDs: {claim_id}")
        if set(evidence_ids) != claim_evidence[claim_id]:
            raise ValueError(f"verification изменила evidence IDs claim: {claim_id}")
        if not isinstance(check.get("reason"), str) or not check["reason"].strip():
            raise ValueError(f"verification reason отсутствует: {claim_id}")
    unanswered = verification.get("unanswered_facts")
    issues = verification.get("issues")
    if not isinstance(unanswered, list) or not isinstance(issues, list):
        raise ValueError("verification completeness fields должны быть массивами")
    if verdict == "pass":
        if checked != claim_ids:
            raise ValueError("verification pass не проверил каждый claim")
        if any(check["status"] != "supported" for check in checks):
            raise ValueError("verification pass содержит неподтверждённый claim")
        if unanswered:
            raise ValueError("verification pass содержит unanswered_facts")
        if issues:
            raise ValueError("verification pass содержит issues")


def _render_citations(generation: dict, evidence: list[dict], *, target_language: str) -> str:
    registry = {item["evidence_id"]: item for item in evidence}
    ordered_ids: list[str] = []
    for claim in generation["claims"]:
        for evidence_id in claim["evidence_ids"]:
            if evidence_id not in registry:
                raise ValueError(f"неизвестная ссылка при публикации: {evidence_id}")
            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)
    answer = generation["answer"].strip()
    for number, evidence_id in enumerate(ordered_ids, 1):
        answer = answer.replace(f"[{evidence_id}]", f"[{number}]")
    if re.search(r"\bE\d{3,}\b", answer):
        raise ValueError("не все evidence IDs преобразованы при публикации")
    if not ordered_ids:
        return answer
    source_title = "Источники" if target_language.lower().startswith("ru") else "Sources"
    lines = [f"{source_title}:"]
    for number, evidence_id in enumerate(ordered_ids, 1):
        item = registry[evidence_id]
        section = str(item.get("section") or "").strip()
        document = str(item.get("document") or "").strip()
        if not document or not section:
            raise ValueError(f"для {evidence_id} отсутствует документ или пункт")
        lines.append(f"[{number}] {document} — {section}")
    return f"{answer}\n\n" + "\n".join(lines)


def _insufficient_evidence_answer(target_language: str) -> str:
    if target_language.lower().startswith("ru"):
        return "Недостаточно подтверждённых данных в предоставленных документах для надёжного ответа."
    return "The supplied documents do not contain enough verified evidence for a reliable answer."


def _load_prompt(filename: str) -> str:
    module_dir = Path(__file__).resolve().parent
    candidates = (
        module_dir / filename,
        module_dir.parent / "core" / "prompts" / filename,
    )
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"не найдена системная инструкция: {filename}")
