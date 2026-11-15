"""Самодостаточный конкурсный RAG-конвейер с claim-level проверкой."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
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
REVISION_MIN_REMAINING_SECONDS = 90.0
RECOVERY_MIN_REMAINING_SECONDS = 60.0
MAX_RENDERED_ANSWER_CHARS = 10_000


class SubmissionPipeline:
    def __init__(self, index: HybridIndex, client: ModelClient, *, top_k: int = 30, evidence_k: int = 10) -> None:
        self.index = index
        self.client = client
        self.top_k = top_k
        self.evidence_k = evidence_k
        self.analysis_prompt = _load_prompt("analysis_v2.txt")
        self.generation_prompt = _load_prompt("generation_v2.txt")
        self.verification_prompt = _load_prompt("verification_v2.txt")
        self.last_outcome: dict[str, object] = {}

    def run(self, question: str) -> str:
        started = perf_counter()
        try:
            stage_started = perf_counter()
            messages = parse_conversation(question)
            self._record_duration("question_parse", stage_started)
            analysis = self._json(
                self.client.complete_json(
                    self.analysis_prompt,
                    json.dumps({"messages": messages}, ensure_ascii=False),
                    stage="analysis",
                )
            )
            _validate_analysis(analysis)
            fact_requirements = _fact_requirements(analysis["requested_facts"])
            stage_started = perf_counter()
            standalone = analysis["standalone_question"].strip()
            lexical = [standalone, *analysis["lexical_queries"]]
            semantic = analysis["semantic_queries"] or [standalone]
            entities = analysis["entities"]
            all_queries = [*lexical, *semantic]
            self._record_duration(
                "query_preparation", stage_started,
                fact_count=len(fact_requirements), query_count=len(all_queries),
            )
            self._record_quality(
                "analysis", context_turns=len(messages), language_detected=1,
                entities_count=len(entities),
                requested_facts_count=len(analysis["requested_facts"]),
                lexical_queries_count=len(analysis["lexical_queries"]),
                semantic_queries_count=len(analysis["semantic_queries"]),
                ambiguous_count=len(analysis["unresolved_ambiguities"]),
                query_diversity=len({str(v).casefold() for v in all_queries}) / len(all_queries),
            )
            fragments = self.index.search_queries(
                lexical, semantic, top_k=self.top_k, final_k=self.evidence_k
            )
            stage_started = perf_counter()
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
            self._record_duration(
                "evidence_selection", stage_started,
                evidence_count=len(evidence),
                fragment_ids=[_evidence_trace_id(item) for item in evidence],
            )
            generation_request = {
                "mode": "initial",
                "target_language": analysis["target_language"],
                "question": standalone,
                "requested_facts": analysis["requested_facts"],
                "fact_requirements": fact_requirements,
                "evidence": evidence,
            }
            generation = self._generate(generation_request, evidence)
            verification = self._verify(
                messages, analysis, generation, evidence, fact_requirements=fact_requirements
            )
            revised = verification["verdict"] != "pass"
            revision_allowed = (
                revised
                and (
                    self.client.budget is None
                    or not self.client.budget.exhausted(REVISION_MIN_REMAINING_SECONDS)
                )
            )
            if revision_allowed:
                if verification.get("unanswered_facts") or generation.get("answer_type") == "insufficient_evidence":
                    evidence = self._expanded_evidence_search(
                        standalone, verification, evidence, fact_requirements
                    )
                    generation_request["evidence"] = evidence
                generation = self._generate(
                    {
                        **generation_request,
                        "mode": "revision",
                        "previous_generation": generation,
                        "verification": verification,
                    },
                    evidence,
                )
                verification = self._verify(
                    messages,
                    analysis,
                    generation,
                    evidence,
                    fact_requirements=fact_requirements,
                    stage="reverification",
                )
                if self.client.metrics is not None:
                    self.client.metrics.record("budget", "revision", executed=1)
            elif revised and self.client.metrics is not None:
                self.client.metrics.record("budget", "revision", executed=0)
            if verification["verdict"] == "pass":
                render_started = perf_counter()
                result = _render_citations(
                    generation, evidence, target_language=analysis["target_language"]
                )
                self._record_duration("citation_rendering", render_started)
                final_started = perf_counter()
                retained_claims = len(generation["claims"])
                answer_state = (
                    "legal_refusal"
                    if generation.get("answer_type") == "insufficient_evidence"
                    else "success"
                )
                self._record_duration("final_rendering", final_started)
            else:
                render_started = perf_counter()
                partial = _render_verified_partial_answer(
                    generation,
                    verification,
                    evidence,
                    target_language=analysis["target_language"],
                )
                if partial is None:
                    result = _insufficient_evidence_answer(analysis["target_language"])
                    retained_claims = 0
                    answer_state = "legal_refusal"
                else:
                    result, retained_claims = partial
                    answer_state = "success"
                self._record_duration(
                    "final_rendering", render_started, partial=int(partial is not None)
                )
            total_claims = len(generation["claims"])
            self.last_outcome = {
                "state": answer_state,
                "retained_claims": retained_claims,
                "total_claims": total_claims,
                "retained_claims_rate": retained_claims / total_claims if total_claims else 0.0,
                "verification": verification["verdict"],
            }
            self._record_quality(
                "question", revised=int(revised), verified_pass=int(verification["verdict"] == "pass"),
                answer_length=len(result), retained_claims=retained_claims,
                retained_claims_rate=retained_claims / total_claims if total_claims else 0.0,
                answer_state=answer_state,
            )
            self._record_question(started, generation, verification, technical_failure=False)
            return result
        except Exception as exc:
            self.last_outcome = {
                "state": "technical_refusal",
                "retained_claims": 0,
                "total_claims": 0,
                "retained_claims_rate": 0.0,
            }
            self._record_question(started, None, None, technical_failure=True, error=exc)
            raise


    def recovery_answer(self, question: str) -> str:
        if self.client.budget is not None:
            self.client.budget.require(
                "verified recovery",
                reserve_seconds=RECOVERY_MIN_REMAINING_SECONDS,
            )
        messages = parse_conversation(question)
        last_question = next((item["content"] for item in reversed(messages) if item["role"] == "user"), question)
        fragments = self.index.search(last_question, top_k=18, final_k=6)
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
        target_language = _question_language(last_question)
        analysis = {
            "target_language": target_language,
            "standalone_question": last_question,
            "requested_facts": [],
        }
        generation = self._generate(
            {
                "mode": "recovery",
                "target_language": target_language,
                "question": last_question,
                "requested_facts": [],
                "evidence": evidence,
            },
            evidence,
        )
        verification = self._verify(messages, analysis, generation, evidence)
        if verification["verdict"] == "pass":
            result = _render_citations(
                generation,
                evidence,
                target_language=target_language,
            )
            state = (
                "legal_refusal"
                if generation.get("answer_type") == "insufficient_evidence"
                else "success"
            )
            retained_claims = len(generation["claims"])
        else:
            partial = _render_verified_partial_answer(
                generation,
                verification,
                evidence,
                target_language=target_language,
            )
            if partial is None:
                raise ValueError("recovery answer was not verified")
            result, retained_claims = partial
            state = "success"
        total_claims = len(generation["claims"])
        self.last_outcome = {
            "state": state,
            "retained_claims": retained_claims,
            "total_claims": total_claims,
            "retained_claims_rate": retained_claims / total_claims if total_claims else 0.0,
            "verification": verification["verdict"],
            "recovery_used": True,
        }
        return result

    def _generate(self, request: dict, evidence: list[dict]) -> dict:
        stage = {
            "revision": "generation_revision",
            "recovery": "generation_recovery",
        }.get(request.get("mode"), "generation")
        generation = self._json(
            self.client.complete_json(
                self.generation_prompt,
                json.dumps(request, ensure_ascii=False),
                stage=stage,
            )
        )
        validation_started = perf_counter()
        _align_generation_citations(generation)
        _validate_generation(generation, evidence)
        _validate_decimal_calculations(generation)
        self._record_duration("structure_validation", validation_started)
        claims = generation["claims"]
        references = [item for claim in claims for item in claim["evidence_ids"]]
        self._record_quality(
            "generation", claims_count=len(claims), citations_count=len(references),
            unique_citations_count=len(set(references)),
            claims_with_citations_rate=sum(bool(c["evidence_ids"]) for c in claims) / len(claims) if claims else 1.0,
            answer_length=len(generation["answer"]),
            language_match_heuristic=_language_match_heuristic(
                generation["answer"], request["target_language"]
            ),
        )
        return generation

    def _verify(
        self,
        messages: list[dict[str, str]],
        analysis: dict,
        generation: dict,
        evidence: list[dict],
        *,
        fact_requirements: list[dict] | None = None,
        stage: str = "verification",
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
                        "fact_requirements": fact_requirements or [],
                        "generation": generation,
                        "evidence": evidence,
                    },
                    ensure_ascii=False,
                ),
                stage=stage,
            )
        )
        _normalize_verification(verification, generation, fact_requirements or [])
        _validate_verification(verification, generation, evidence, fact_requirements or [])
        checks = verification["claim_checks"]
        self._record_quality(
            "verification", checked_claims_count=len(checks),
            verification_coverage=len(checks) / len(generation["claims"]) if generation["claims"] else 1.0,
            self_reported_supported_rate=sum(c["status"] == "supported" for c in checks) / len(checks) if checks else 0.0,
            unsupported_claims_count=sum(c["status"] != "supported" for c in checks),
            unanswered_facts_count=len(verification["unanswered_facts"]),
            issues_count=len(verification["issues"]),
            passed=int(verification["verdict"] == "pass"),
            fact_checks_count=len(verification.get("fact_checks", [])),
            fact_coverage=sum(
                check.get("status") == "addressed"
                for check in verification.get("fact_checks", [])
            ) / len(fact_requirements) if fact_requirements else 1.0,
        )
        return verification

    def _expanded_evidence_search(
        self,
        standalone: str,
        verification: dict,
        evidence: list[dict],
        fact_requirements: list[dict],
    ) -> list[dict]:
        started = perf_counter()
        missing = [str(item).strip() for item in verification.get("unanswered_facts", []) if str(item).strip()]
        requirement_by_id = {item["fact_id"]: item["description"] for item in fact_requirements}
        for check in verification.get("fact_checks", []):
            if check.get("status") != "addressed":
                description = requirement_by_id.get(str(check.get("fact_id")))
                if description and description not in missing:
                    missing.append(description)
        if not missing:
            return evidence
        fragments = self.index.search_queries(
            [standalone, *missing], missing, top_k=self.top_k, final_k=self.evidence_k
        )
        merged: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [
            *evidence,
            *(
                {
                    "evidence_id": "",
                    "document": fragment.source,
                    "section": self.index.verified_citation_section(fragment),
                    "dates": fragment.dates,
                    "relations": fragment.relations,
                    "text": fragment.text,
                }
                for fragment in fragments
            ),
        ]:
            key = (str(item["document"]), str(item["section"]), str(item["text"]))
            if key in seen:
                continue
            seen.add(key)
            merged.append({**item, "evidence_id": f"E{len(merged) + 1:03d}"})
            if len(merged) >= self.evidence_k * 2:
                break
        self.client.recording.checkpoint("evidence.expanded", merged)
        self._record_duration(
            "expanded_search", started, missing_fact_count=len(missing),
            evidence_count=len(merged), fragment_ids=[_evidence_trace_id(item) for item in merged],
        )
        return merged

    def _record_duration(self, stage: str, started: float, **fields: object) -> None:
        if self.client.metrics is not None:
            self.client.metrics.record(
                "pipeline", stage,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                error_count=0, retry_count=0, **fields,
            )

    def _record_quality(self, stage: str, **fields: object) -> None:
        if self.client.metrics is not None:
            self.client.metrics.record("quality", stage, **fields)

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
        text = value.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            data = None
            for pos, char in enumerate(text[:256]):
                if char != "{":
                    continue
                try:
                    data, _ = decoder.raw_decode(text[pos:])
                    break
                except json.JSONDecodeError:
                    pass
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
    answer_type = generation.get("answer_type", "answer")
    if answer_type not in {"answer", "insufficient_evidence"}:
        raise ValueError("неподдерживаемый тип ответа")
    if not claims:
        if answer_type != "insufficient_evidence" or not generation["missing_facts"]:
            raise ValueError("публикация ответа без structured claims запрещена")
        if re.search(r"\bE\d{3,}\b", generation["answer"]):
            raise ValueError("отказ без утверждений не может содержать evidence ID")
        return
    if answer_type != "answer":
        raise ValueError("юридические утверждения несовместимы с полным отказом")
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


def _align_generation_citations(generation: dict) -> None:
    """Render the user answer from atomic claims and their structured evidence IDs."""
    claims = generation.get("claims")
    if not isinstance(claims, list):
        return
    if not claims:
        if generation.get("answer_type") == "insufficient_evidence" and isinstance(
            generation.get("answer"), str
        ):
            generation["answer"] = re.sub(
                r"\s*\[E\d{3,}\]", "", generation["answer"]
            ).strip()
        return
    rendered: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            return
        text = claim.get("text")
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(text, str) or not text.strip() or not isinstance(evidence_ids, list):
            return
        markers = " ".join(f"[{item}]" for item in evidence_ids if isinstance(item, str))
        rendered.append(f"{text.strip()} {markers}".rstrip())
    generation["answer"] = "\n".join(rendered)


def _validate_verification(
    verification: dict,
    generation: dict,
    evidence: list[dict],
    fact_requirements: list[dict] | None = None,
) -> None:
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
    requirements = fact_requirements or []
    fact_checks = verification.get("fact_checks", [])
    if not isinstance(fact_checks, list):
        raise ValueError("verification.fact_checks должен быть массивом")
    expected_fact_ids = {item["fact_id"] for item in requirements}
    checked_fact_ids: set[str] = set()
    for check in fact_checks:
        if (
            not isinstance(check, dict)
            or check.get("fact_id") not in expected_fact_ids
            or check.get("fact_id") in checked_fact_ids
            or check.get("status") not in {"addressed", "unsupported", "missing"}
            or not isinstance(check.get("claim_ids", []), list)
            or not set(check.get("claim_ids", [])) <= claim_ids
        ):
            raise ValueError("verification содержит некорректную проверку обязательного факта")
        checked_fact_ids.add(check["fact_id"])
    if verdict == "pass":
        if generation.get("answer_type") == "insufficient_evidence" and (
            generation["claims"] or not generation.get("missing_facts")
        ):
            raise ValueError("отказ недостаточно обоснован")
        if checked != claim_ids:
            raise ValueError("verification pass не проверил каждый claim")
        if any(check["status"] != "supported" for check in checks):
            raise ValueError("verification pass содержит неподтверждённый claim")
        if unanswered:
            raise ValueError("verification pass содержит unanswered_facts")
        if issues:
            raise ValueError("verification pass содержит issues")
        if checked_fact_ids != expected_fact_ids or any(
            check["status"] != "addressed" for check in fact_checks
        ):
            raise ValueError("verification pass не подтвердил каждый обязательный факт")


def _normalize_verification(
    verification: dict,
    generation: dict,
    fact_requirements: list[dict] | None = None,
) -> None:
    """Downgrade inconsistent verifier output without accepting unsupported claims."""
    checks = verification.get("claim_checks")
    claims = generation.get("claims")
    if not isinstance(checks, list) or not isinstance(claims, list):
        return
    claim_evidence = {
        claim.get("claim_id"): list(claim.get("evidence_ids", []))
        for claim in claims
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    }
    normalized: list[dict] = []
    seen: set[str] = set()
    inconsistent = False
    for check in checks:
        if not isinstance(check, dict):
            inconsistent = True
            continue
        claim_id = check.get("claim_id")
        if claim_id not in claim_evidence or claim_id in seen:
            inconsistent = True
            continue
        seen.add(claim_id)
        expected = claim_evidence[claim_id]
        if check.get("evidence_ids") != expected:
            check = {
                **check,
                "status": "unsupported",
                "evidence_ids": expected,
                "reason": "Verifier changed the claim evidence mapping; revision is required.",
            }
            inconsistent = True
        normalized.append(check)
    verification["claim_checks"] = normalized
    requirements = fact_requirements or []
    expected_fact_ids = {item["fact_id"] for item in requirements}
    fact_checks = verification.get("fact_checks")
    if not isinstance(fact_checks, list):
        fact_checks = []
    normalized_facts: list[dict] = []
    seen_facts: set[str] = set()
    known_claim_ids = set(claim_evidence)
    fact_status_aliases = {
        "addressed": "addressed",
        "answered": "addressed",
        "supported": "addressed",
        "complete": "addressed",
        "unsupported": "unsupported",
        "partially_supported": "unsupported",
        "insufficient_evidence": "unsupported",
        "missing": "missing",
        "unanswered": "missing",
        "not_addressed": "missing",
    }
    for check in fact_checks:
        if not isinstance(check, dict) or check.get("fact_id") not in expected_fact_ids or check.get("fact_id") in seen_facts:
            inconsistent = True
            continue
        status = fact_status_aliases.get(str(check.get("status", "")).casefold())
        if status is None:
            status = "missing"
            inconsistent = True
        raw_claim_ids = check.get("claim_ids", [])
        claim_ids = [
            claim_id for claim_id in raw_claim_ids
            if isinstance(raw_claim_ids, list) and claim_id in known_claim_ids
        ] if isinstance(raw_claim_ids, list) else []
        if claim_ids != raw_claim_ids:
            inconsistent = True
        check = {
            **check,
            "status": status,
            "claim_ids": claim_ids,
            "reason": str(check.get("reason") or "Required fact check normalized."),
        }
        seen_facts.add(check["fact_id"])
        normalized_facts.append(check)
    for requirement in requirements:
        if requirement["fact_id"] not in seen_facts:
            normalized_facts.append({
                "fact_id": requirement["fact_id"],
                "status": "missing",
                "claim_ids": [],
                "reason": "Required fact was not explicitly checked.",
            })
            inconsistent = True
    verification["fact_checks"] = normalized_facts
    if set(claim_evidence) != seen:
        inconsistent = True
    if verification.get("verdict") == "pass" and (
        inconsistent
        or verification.get("unanswered_facts")
        or verification.get("issues")
        or any(check.get("status") != "supported" for check in normalized)
        or any(check.get("status") != "addressed" for check in normalized_facts)
    ):
        verification["verdict"] = "revision_required"


def _render_verified_partial_answer(
    generation: dict,
    verification: dict,
    evidence: list[dict],
    *,
    target_language: str,
) -> tuple[str, int] | None:
    """Publish only completely checked supported claims without another model call."""
    if verification.get("verdict") != "revision_required":
        return None
    claims = generation.get("claims", [])
    checks = verification.get("claim_checks", [])
    claim_ids = {claim["claim_id"] for claim in claims}
    if {check.get("claim_id") for check in checks} != claim_ids:
        return None
    supported_ids = {
        check["claim_id"]
        for check in checks
        if check.get("status") == "supported"
    }
    if not supported_ids:
        return None
    registry = {item["evidence_id"]: item for item in evidence}
    retained: list[dict] = []
    for claim in claims:
        if claim["claim_id"] not in supported_ids:
            continue
        if any(
            evidence_id not in registry
            or not str(registry[evidence_id].get("document") or "").strip()
            or not str(registry[evidence_id].get("section") or "").strip()
            for evidence_id in claim["evidence_ids"]
        ):
            return None
        candidate_claims = [*retained, claim]
        candidate_generation = {
            "answer": "\n".join(
                f"{item['text'].strip()} "
                + " ".join(f"[{evidence_id}]" for evidence_id in item["evidence_ids"])
                for item in candidate_claims
            ),
            "claims": candidate_claims,
        }
        rendered = _render_citations(
            candidate_generation,
            evidence,
            target_language=target_language,
        )
        if len(rendered) > MAX_RENDERED_ANSWER_CHARS:
            break
        retained.append(claim)
    if not retained:
        return None
    partial_generation = {
        "answer": "\n".join(
            f"{claim['text'].strip()} "
            + " ".join(f"[{evidence_id}]" for evidence_id in claim["evidence_ids"])
            for claim in retained
        ),
        "claims": retained,
    }
    rendered = _render_citations(
            partial_generation,
            evidence,
            target_language=target_language,
        )
    limitations = [_clean_limitation(item) for item in verification.get("unanswered_facts", [])]
    limitations = [item for item in limitations if item]
    limitations.extend(
        cleaned for item in generation.get("missing_facts", [])
        if (cleaned := _clean_limitation(item)) and cleaned not in limitations
    )
    if limitations:
        label = "Не подтверждено документами" if target_language.lower().startswith("ru") else "Not established by the supplied documents"
        rendered += "\n\n" + label + ": " + "; ".join(limitations) + "."
    return rendered, len(retained)


def _clean_limitation(value: object) -> str:
    text = str(value).strip()
    text = re.sub(r"^F\d{3,}\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" .;:")


def _fact_requirements(requested_facts: list[object]) -> list[dict[str, str]]:
    requirements: list[dict[str, str]] = []
    for item in requested_facts:
        description = str(item).strip()
        if description:
            requirements.append({
                "fact_id": f"F{len(requirements) + 1:03d}",
                "description": description,
            })
    return requirements


def _evidence_trace_id(item: dict) -> str:
    import hashlib

    payload = "\u241f".join(
        str(item.get(name) or "") for name in ("document", "section", "text")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _validate_decimal_calculations(generation: dict) -> None:
    calculations = generation.get("calculations", [])
    if calculations is None:
        return
    if not isinstance(calculations, list):
        raise ValueError("generation.calculations должен быть массивом")
    for row in calculations:
        if not isinstance(row, dict) or row.get("operation") not in {"add", "subtract", "multiply", "divide", "percent"}:
            raise ValueError("неподдерживаемая детерминированная операция")
        try:
            operands = [Decimal(str(value)) for value in row.get("operands", [])]
            claimed = Decimal(str(row["result"]))
            operation = row["operation"]
            if not operands:
                raise InvalidOperation
            if operation == "add":
                actual = sum(operands, Decimal("0"))
            elif operation == "subtract" and len(operands) == 2:
                actual = operands[0] - operands[1]
            elif operation == "multiply":
                actual = Decimal("1")
                for operand in operands:
                    actual *= operand
            elif operation == "divide" and len(operands) == 2:
                actual = operands[0] / operands[1]
            elif operation == "percent" and len(operands) == 2:
                actual = operands[0] * operands[1] / Decimal("100")
            else:
                raise InvalidOperation
        except (InvalidOperation, KeyError, ZeroDivisionError):
            raise ValueError("некорректные операнды детерминированного вычисления") from None
        if actual != claimed:
            raise ValueError("числовой результат не совпадает с детерминированным вычислением")


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
    try:
        from prompts import PROMPTS
    except ImportError:
        prompt_path = Path(__file__).resolve().parents[1] / "core" / "prompts" / filename
        return prompt_path.read_text(encoding="utf-8").strip()
    return PROMPTS[filename].strip()


def _language_match_heuristic(answer: str, language: str) -> float | None:
    letters = [c for c in answer if c.isalpha()]
    if not letters:
        return None
    cyrillic = sum("\u0400" <= c <= "\u04ff" for c in letters)
    latin = sum(("a" <= c.lower() <= "z") for c in letters)
    main_language = language.casefold().split("-")[0]
    if main_language in {"ru", "uk", "be"}:
        return float(cyrillic >= latin)
    if main_language == "en":
        return float(latin >= cyrillic)
    return None


def _question_language(question: str) -> str:
    letters = [character for character in question if character.isalpha()]
    cyrillic = sum("\u0400" <= character <= "\u04ff" for character in letters)
    latin = sum("a" <= character.casefold() <= "z" for character in letters)
    return "ru" if cyrillic >= latin else "en"
