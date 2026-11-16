from __future__ import annotations

import json
import logging
import math
import threading
from copy import deepcopy
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from backend.schemas import CoreResult, ModelQuestion
from backend.core_gateway import InvalidCoreOutput
from integration.config import IntegrationConfig
from integration.routing import ROUTING_PROMPT, RouteDecision, parse_route
from submission.model_client import ModelClient
from submission.budget import RuntimeBudget
from submission.pipeline import SubmissionPipeline
from submission.search import Fragment, HybridIndex, RequiredLocalServiceError, _read_archive_documents

LOGGER = logging.getLogger("legal_rag.integration")


class QueueFullError(RuntimeError):
    pass


class ModelDependencyError(RuntimeError):
    def __init__(self, service: str, reason: str) -> None:
        super().__init__(f"{service}: {reason}")
        self.service = service
        self.reason = reason


class RagHandler:
    """One verified index and a bounded model queue per application instance."""

    def __init__(
        self,
        config: IntegrationConfig,
        *,
        client: ModelClient | None = None,
        index: HybridIndex | None = None,
    ) -> None:
        self.config = config
        self.client = client or ModelClient(timeout=config.model_timeout_seconds)
        self.router_client = ModelClient(timeout=30.0, temperature=0.3)
        self._router_lock = threading.Lock()
        self.index = index or HybridIndex.from_prebuilt(
            config.index_dir, config.corpus_path, self.client
        )
        _hashes, self.documents = _read_archive_documents(config.corpus_path)
        self.pipeline = SubmissionPipeline(
            self.index,
            self.client,
            top_k=config.top_k,
            evidence_k=config.evidence_k,
        )
        self._slots = threading.BoundedSemaphore(config.queue_capacity)
        self._model_lock = threading.BoundedSemaphore(config.max_concurrency)
        self._state_lock = threading.RLock()
        self._probe_lock = threading.Lock()
        self._probe_stop = threading.Event()
        self.diagnostics = self._probe_dependencies()
        self.ready = bool(self.diagnostics["ready"])
        self._next_probe_at = monotonic() + self._next_probe_delay(self.ready)
        self._probe_thread = threading.Thread(
            target=self._readiness_loop, name="rag-readiness", daemon=True
        )
        self._probe_thread.start()

    def close(self) -> None:
        if hasattr(self, "_probe_stop"):
            self._probe_stop.set()
        if hasattr(self, "_state_lock"):
            with self._state_lock:
                self.ready = False
        else:
            self.ready = False

    def readiness_snapshot(self) -> dict[str, Any]:
        self._refresh_readiness_if_due()
        if not hasattr(self, "_state_lock"):
            return deepcopy(getattr(self, "diagnostics", {"ready": self.ready}))
        with self._state_lock:
            return deepcopy(self.diagnostics)

    def _next_probe_delay(self, ready: bool) -> float:
        return (
            self.config.readiness_probe_interval_seconds
            if ready
            else self.config.readiness_retry_interval_seconds
        )

    def _readiness_loop(self) -> None:
        while not self._probe_stop.wait(self.config.readiness_retry_interval_seconds):
            self._refresh_readiness_if_due()

    def _refresh_readiness_if_due(self) -> bool:
        if not hasattr(self, "_probe_lock"):
            return False
        now = monotonic()
        with self._state_lock:
            if now < self._next_probe_at:
                return False
        if not self._probe_lock.acquire(blocking=False):
            return False
        try:
            now = monotonic()
            with self._state_lock:
                if now < self._next_probe_at:
                    return False
            model_lock = getattr(self, "_model_lock", None)
            if model_lock is not None and not model_lock.acquire(blocking=False):
                return False
            try:
                diagnostics = self._probe_dependencies()
            finally:
                if model_lock is not None:
                    model_lock.release()
            ready = bool(diagnostics["ready"])
            with self._state_lock:
                self.diagnostics = diagnostics
                self.ready = ready
                self._next_probe_at = monotonic() + self._next_probe_delay(ready)
            return True
        finally:
            self._probe_lock.release()

    def _is_ready(self) -> bool:
        self._refresh_readiness_if_due()
        if not hasattr(self, "_state_lock"):
            return self.ready
        with self._state_lock:
            return self.ready

    def _mark_dependency_unavailable(self, service: str, error: Exception) -> None:
        if not hasattr(self, "_state_lock"):
            return
        with self._state_lock:
            diagnostics = deepcopy(self.diagnostics)
            diagnostics.setdefault(service, {})
            diagnostics[service] = {"ready": False, "error_type": type(error).__name__}
            diagnostics["ready"] = False
            self.diagnostics = diagnostics
            self.ready = False
            self._next_probe_at = monotonic()

    def route_question(self, question: str, history: list[dict[str, str]]) -> RouteDecision:
        if not self._router_lock.acquire(timeout=30.0):
            raise ModelDependencyError("deepseek", "routing queue timed out")
        try:
            self.router_client.budget = RuntimeBudget.start(30.0)
            try:
                request = {"question": question, "history": history}
                for attempt in range(2):
                    raw = self.router_client.complete_json(
                        ROUTING_PROMPT,
                        json.dumps(request, ensure_ascii=False),
                        stage="routing" if attempt == 0 else "routing_format_retry",
                    )
                    try:
                        return parse_route(raw)
                    except ValueError as exc:
                        if attempt:
                            raise InvalidCoreOutput("Conversation routing returned an invalid decision") from exc
                        request = {**request, "format_correction": {"error": str(exc), "previous_response": raw}}
            finally:
                self.router_client.budget = None
        except InvalidCoreOutput:
            raise
        except (httpx.HTTPError, RuntimeError, TimeoutError) as exc:
            LOGGER.warning("conversation_route_failure error_type=%s", type(exc).__name__)
            raise ModelDependencyError("deepseek", "conversation routing failed") from exc
        finally:
            self._router_lock.release()

    def __call__(self, payload: str) -> CoreResult:
        if not self._is_ready():
            raise ModelDependencyError("rag", "one or more required model dependencies are unavailable")
        if not self._slots.acquire(blocking=False):
            raise QueueFullError("RAG request queue is full")
        started = perf_counter()
        try:
            request = ModelQuestion.model_validate_json(payload)
            messages = [item.model_dump() for item in request.recent_messages]
            context = request.context.model_dump(mode="json")
            context_note = json.dumps(
                {
                    "active_document": context.get("active_document"),
                    "event_date": context.get("event_date"),
                    "summary": request.summary,
                    "context_revision": request.context_revision,
                },
                ensure_ascii=False,
            )
            messages.append({"role": "user", "content": f"Контекст: {context_note}\n\n{request.question}"})
            try:
                budget = RuntimeBudget.start(min(
                    self.config.request_timeout_seconds,
                    request.remaining_budget_seconds or self.config.request_timeout_seconds,
                ))
                acquired = self._model_lock.acquire(timeout=budget.remaining())
                if not acquired:
                    raise TimeoutError("RAG request expired while waiting for the model queue")
                try:
                    self.client.reset_question_trace()
                    previous_budget = self.client.budget
                    self.client.budget = budget
                    try:
                        self.pipeline.run(json.dumps({"messages": messages, "answer_style": request.answer_style}, ensure_ascii=False))
                        artifacts = deepcopy(self.pipeline.last_artifacts)
                        result = self._to_core_result(request, artifacts)
                    finally:
                        self.client.budget = previous_budget
                finally:
                    self._model_lock.release()
            except RequiredLocalServiceError as exc:
                self._mark_dependency_unavailable("local_search", exc)
                LOGGER.exception("rag_model_failure service=local_model error_type=%s", type(exc).__name__)
                raise ModelDependencyError("local_model", "request failed") from exc
            except TimeoutError as exc:
                LOGGER.exception("rag_request_timeout error_type=%s", type(exc).__name__)
                raise ModelDependencyError("rag", "request deadline exceeded") from exc
            except httpx.HTTPError as exc:
                self._mark_dependency_unavailable("deepseek", exc)
                LOGGER.exception("rag_model_failure service=deepseek error_type=%s", type(exc).__name__)
                raise ModelDependencyError("deepseek", "invalid or unavailable response") from exc
            except ValueError as exc:
                LOGGER.exception("rag_invalid_model_output error_type=%s", type(exc).__name__)
                raise InvalidCoreOutput("RAG returned an invalid verified answer") from exc
            LOGGER.info(
                "rag_request_complete request_id=%s duration_ms=%.3f sources=%d stages=%s trace=%s",
                request.request_id,
                (perf_counter() - started) * 1000,
                len(result.source_locations),
                json.dumps(artifacts.get("stage_timings_ms", {}), sort_keys=True),
                json.dumps(self.client.question_trace(), sort_keys=True),
            )
            return result
        finally:
            self._slots.release()

    def _probe_dependencies(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "bge_m3": {"ready": False},
            "qwen3": {"ready": False},
            "deepseek": {"ready": False},
            "index": {"ready": self.index.faiss is not None},
        }
        try:
            response = httpx.post(
                f"{self.client.embedding_base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.client.embedding_api_key}"},
                json={"model": self.client.embedding_model, "input": ["legal readiness probe"]},
                timeout=self.config.readiness_probe_timeout_seconds,
            )
            response.raise_for_status()
            embedding = response.json().get("data", [{}])[0].get("embedding", [])
            valid = len(embedding) == 1024 and all(math.isfinite(float(value)) for value in embedding)
            result["bge_m3"] = {"ready": valid, "dimension": len(embedding)}
        except Exception as exc:
            LOGGER.warning("model_probe service=bge_m3 error_type=%s", type(exc).__name__)
            result["bge_m3"] = {"ready": False, "error_type": type(exc).__name__, "http_status": getattr(getattr(exc, "response", None), "status_code", None), "reason": str(exc)[:160]}
        try:
            response = httpx.post(
                f"{self.client.reranker_base_url}/rerank",
                headers={"Authorization": f"Bearer {self.client.reranker_api_key}"},
                json={
                    "model": self.client.reranker_model,
                    "query": "legal readiness probe",
                    "documents": ["first legal fragment", "second legal fragment"],
                    "top_n": 2,
                },
                timeout=self.config.readiness_probe_timeout_seconds,
            )
            response.raise_for_status()
            rows = response.json().get("results", response.json().get("data", []))
            valid = isinstance(rows, list) and bool(rows) and all(
                isinstance(row, dict) and 0 <= int(row.get("index", row.get("document_index", -1))) < 2
                for row in rows
            )
            result["qwen3"] = {"ready": valid, "results": len(rows) if isinstance(rows, list) else 0}
        except Exception as exc:
            LOGGER.warning("model_probe service=qwen3 error_type=%s", type(exc).__name__)
            result["qwen3"] = {"ready": False, "error_type": type(exc).__name__, "http_status": getattr(getattr(exc, "response", None), "status_code", None), "reason": str(exc)[:160]}
        try:
            if not self.client.base_url or not self.client.api_key:
                raise RuntimeError("DeepSeek configuration is incomplete")
            previous_budget = self.client.budget
            self.client.budget = RuntimeBudget.start(self.config.readiness_probe_timeout_seconds)
            try:
                self.client.complete_json(
                    "Return a JSON object with a single key ready and boolean value true.",
                    "{}",
                    stage="readiness",
                )
            finally:
                self.client.budget = previous_budget
            result["deepseek"] = {"ready": True, "model": self.client.llm_model}
        except Exception as exc:
            LOGGER.warning("model_probe service=deepseek error_type=%s", type(exc).__name__)
            result["deepseek"] = {"ready": False, "error_type": type(exc).__name__}
        result["ready"] = all(
            bool(result[name].get("ready"))
            for name in ("index", "bge_m3", "qwen3", "deepseek")
        )
        return result

    def _to_core_result(self, request: ModelQuestion, artifacts: dict[str, Any]) -> CoreResult:
        generation = artifacts.get("generation")
        verification = artifacts.get("verification")
        evidence = artifacts.get("evidence")
        fragments = artifacts.get("fragments")
        evidence_fragments = artifacts.get("evidence_fragments")
        if not all(isinstance(value, (dict, list)) for value in (generation, verification, evidence, fragments)):
            raise ValueError("RAG did not expose a structured verified result")
        generation = generation if isinstance(generation, dict) else {}
        verification = verification if isinstance(verification, dict) else {}
        evidence = evidence if isinstance(evidence, list) else []
        fragments = fragments if isinstance(fragments, list) else []
        checks = {
            item.get("claim_id"): item
            for item in verification.get("claim_checks", [])
            if isinstance(item, dict)
        }
        claims = [
            item for item in generation.get("claims", [])
            if isinstance(item, dict) and checks.get(item.get("claim_id"), {}).get("status") == "supported"
        ]
        presented_claim_ids = {
            str(claim_id)
            for block in verification.get("presentation_blocks", [])
            if isinstance(block, dict)
            for segment in block.get("segments", [])
            if isinstance(segment, dict)
            for claim_id in segment.get("claim_ids", [])
        }
        if presented_claim_ids:
            claims = [item for item in claims if str(item.get("claim_id")) in presented_claim_ids]
        used_ids = []
        for claim in claims:
            for evidence_id in claim.get("evidence_ids", []):
                if evidence_id not in used_ids:
                    used_ids.append(evidence_id)
        if not isinstance(evidence_fragments, dict):
            raise ValueError("RAG did not expose stable evidence-to-fragment mapping")
        by_id = {
            item.get("evidence_id"): (item, evidence_fragments.get(str(item.get("evidence_id"))))
            for item in evidence
        }
        sources: list[dict[str, Any]] = []
        locations: list[dict[str, Any]] = []
        source_ids: dict[str, str] = {}
        for number, evidence_id in enumerate(used_ids, 1):
            pair = by_id.get(evidence_id)
            if pair is None or not isinstance(pair[1], Fragment):
                continue
            item, fragment = pair
            location = self._source_location(fragment, request.index_generation_id)
            if location is None:
                continue
            answer_source_id = f"src-{uuid5(NAMESPACE_URL, request.request_id + ':' + evidence_id).hex}"
            source_ids[evidence_id] = answer_source_id
            sources.append({
                "answer_source_id": answer_source_id,
                "number": number,
                "document_title": Path(fragment.source).stem,
                "version_label": request.index_generation_id,
                "section_number": str(item.get("section") or fragment.section) or None,
            })
            locations.append({"answer_source_id": answer_source_id, **location})
        segments = []
        for claim in claims:
            citations = [
                {"answer_source_id": source_ids[eid], "number": next(s["number"] for s in sources if s["answer_source_id"] == source_ids[eid])}
                for eid in claim.get("evidence_ids", []) if eid in source_ids
            ]
            if citations:
                segments.append({"claim_id": str(claim["claim_id"]), "text": str(claim["text"]), "citations": citations})
        presentation_blocks = []
        supported_ids = {str(claim["claim_id"]) for claim in claims}
        for block in verification.get("presentation_blocks", []):
            if not isinstance(block, dict) or not isinstance(block.get("segments"), list):
                continue
            rendered_segments = []
            for presentation in block["segments"]:
                if not isinstance(presentation, dict):
                    continue
                claim_ids = [str(value) for value in presentation.get("claim_ids", [])]
                evidence_ids = [str(value) for value in presentation.get("evidence_ids", [])]
                if not claim_ids or not set(claim_ids) <= supported_ids:
                    continue
                citations = [
                    {"answer_source_id": source_ids[eid], "number": next(source["number"] for source in sources if source["answer_source_id"] == source_ids[eid])}
                    for eid in evidence_ids if eid in source_ids
                ]
                if citations and str(presentation.get("text", "")).strip():
                    rendered_segments.append({"claim_id": claim_ids[0], "text": str(presentation["text"]).strip(), "citations": citations})
            if rendered_segments:
                presentation_blocks.append({"block_type": block.get("block_type", "conclusion"), "segments": rendered_segments})
        if request.answer_style in {"brief", "standard"} and presentation_blocks:
            presentation_blocks = [{
                "block_type": "conclusion",
                "segments": [segment for block in presentation_blocks for segment in block["segments"]],
            }]
        elif request.answer_style == "detailed" and len(presentation_blocks) == 1:
            segments_for_detail = presentation_blocks[0]["segments"]
            if len(segments_for_detail) > 1:
                presentation_blocks = [
                    {"block_type": "conclusion", "segments": segments_for_detail[:1]},
                    {"block_type": "justification", "segments": segments_for_detail[1:]},
                ]
        missing = [str(value) for value in generation.get("missing_facts", [])]
        missing.extend(str(value) for value in verification.get("unanswered_facts", []) if str(value) not in missing)
        if segments and missing:
            legal_status = "partial"
        elif segments:
            legal_status = "answered"
        else:
            legal_status = "insufficient_evidence"
        verdict = verification.get("verdict")
        verification_status = "passed" if verdict == "pass" else ("partial" if segments else "failed")
        answer_id = str(uuid4())
        answer = {
            "schema_version": "1.0",
            "answer_id": answer_id,
            "legal_status": legal_status,
            "verification_status": verification_status,
            "missing_information": missing,
            "false_premises": [],
            "clarification_question": None,
            "blocks": (presentation_blocks or [{"block_type": "conclusion", "segments": segments}] if segments else [{
                "block_type": "qualification",
                "segments": [{"claim_id": "C000", "text": str(generation.get("answer", "Недостаточно подтверждённых сведений.")), "citations": []}],
            }]),
            "sources": sources,
            "retrieval_audit": {"performed": True, "passes": ["bm25", "bge-m3", "faiss", "rrf", "qwen3", "verification"]},
        }
        return CoreResult.model_validate({"answer": answer, "source_locations": locations})

    def _source_location(self, fragment: Fragment, generation_id: str) -> dict[str, Any] | None:
        text = self.documents.get(fragment.source)
        if text is None or fragment.start_char is None or fragment.end_char is None:
            return None
        if not 0 <= fragment.start_char < fragment.end_char <= len(text):
            return None
        if text[fragment.start_char:fragment.end_char] != fragment.text:
            return None
        document_id = uuid5(NAMESPACE_URL, f"internal-document:{fragment.source}")
        version_id = uuid5(NAMESPACE_URL, f"internal-version:{fragment.source}:{generation_id}")
        clause_id = uuid5(NAMESPACE_URL, f"internal-clause:{fragment.source}:{fragment.section}:{fragment.start_char}:{fragment.end_char}")
        return {
            "document_id": document_id,
            "document_version_id": version_id,
            "clause_id": clause_id,
            "canonical_text": text,
            "start_char": fragment.start_char,
            "end_char": fragment.end_char,
        }
