from __future__ import annotations

import json
import logging
import math
import threading
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from backend.schemas import CoreResult, ModelQuestion
from integration.config import IntegrationConfig
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
        self.diagnostics = self._probe_dependencies()
        self.ready = bool(self.diagnostics["ready"])

    def close(self) -> None:
        self.ready = False

    def __call__(self, payload: str) -> CoreResult:
        if not self.ready:
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
                budget = RuntimeBudget.start(self.config.request_timeout_seconds)
                acquired = self._model_lock.acquire(timeout=budget.remaining())
                if not acquired:
                    raise TimeoutError("RAG request expired while waiting for the model queue")
                try:
                    self.client.reset_question_trace()
                    previous_budget = self.client.budget
                    self.client.budget = budget
                    try:
                        self.pipeline.run(json.dumps({"messages": messages}, ensure_ascii=False))
                        artifacts = deepcopy(self.pipeline.last_artifacts)
                        result = self._to_core_result(request, artifacts)
                    finally:
                        self.client.budget = previous_budget
                finally:
                    self._model_lock.release()
            except RequiredLocalServiceError as exc:
                LOGGER.exception("rag_model_failure service=local_model error_type=%s", type(exc).__name__)
                raise ModelDependencyError("local_model", "request failed") from exc
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                LOGGER.exception("rag_model_failure service=model error_type=%s", type(exc).__name__)
                raise ModelDependencyError("model", "invalid or unavailable response") from exc
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
            "faiss": {"ready": self.index.faiss is not None},
        }
        try:
            response = httpx.post(
                f"{self.client.embedding_base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.client.embedding_api_key}"},
                json={"model": self.client.embedding_model, "input": ["legal readiness probe"]},
                timeout=15.0,
            )
            response.raise_for_status()
            embedding = response.json().get("data", [{}])[0].get("embedding", [])
            valid = len(embedding) == 1024 and all(math.isfinite(float(value)) for value in embedding)
            result["bge_m3"] = {"ready": valid, "dimension": len(embedding)}
        except Exception as exc:
            LOGGER.warning("model_probe service=bge_m3 error_type=%s", type(exc).__name__)
            result["bge_m3"] = {"ready": False, "error_type": type(exc).__name__}
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
                timeout=30.0,
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
            result["qwen3"] = {"ready": False, "error_type": type(exc).__name__}
        try:
            if not self.client.base_url or not self.client.api_key:
                raise RuntimeError("DeepSeek configuration is incomplete")
            self.client.complete_json(
                "Return a JSON object with a single key ready and boolean value true.",
                "{}",
                stage="readiness",
            )
            result["deepseek"] = {"ready": True, "model": self.client.llm_model}
        except Exception as exc:
            LOGGER.warning("model_probe service=deepseek error_type=%s", type(exc).__name__)
            result["deepseek"] = {"ready": False, "error_type": type(exc).__name__}
        result["ready"] = all(bool(item.get("ready")) for item in result.values())
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
            "blocks": ([{"block_type": "conclusion", "segments": segments}] if segments else [{
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
