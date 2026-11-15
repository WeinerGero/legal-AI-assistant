from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from backend.app import app as backend_app
from integration.config import IntegrationConfig
from integration.handler import ModelDependencyError, QueueFullError, RagHandler
from submission.search import RequiredLocalServiceError


def create_app(config: IntegrationConfig | None = None) -> FastAPI:
    """Attach the RAG explicitly while preserving the backend app and routes."""
    app = backend_app
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def integrated_lifespan(application: FastAPI):
        async with original_lifespan(application):
            handler = RagHandler(config or IntegrationConfig.from_env())
            application.state.core_handler = handler
            application.state.rag_ready = handler.ready
            application.state.rag_diagnostics = handler.diagnostics
            try:
                yield
            finally:
                application.state.rag_ready = False
                application.state.core_handler = None
                handler.close()

    app.router.lifespan_context = integrated_lifespan

    if not any(getattr(route, "path", None) == "/integration/readyz" for route in app.routes):
        @app.get("/integration/readyz")
        def integration_ready() -> dict[str, object]:
            return {
                "rag_ready": bool(getattr(app.state, "rag_ready", False)),
                "corpus_id": os.environ.get("CORPUS_ID", ""),
                "index_generation_id": os.environ.get("INDEX_GENERATION_ID", ""),
                "llm_model": os.environ.get("LLM_MODEL", ""),
                "dependencies": getattr(app.state, "rag_diagnostics", {}),
            }
    async def dependency_error(request: Request, exc: ModelDependencyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error_code": "model_unavailable", "message": "Required model dependency is unavailable."})

    async def queue_error(request: Request, exc: QueueFullError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error_code": "model_queue_full", "message": "RAG request queue is full."})

    async def local_service_error(request: Request, exc: RequiredLocalServiceError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"error_code": "model_unavailable", "message": "Required local model service is unavailable."})

    app.add_exception_handler(ModelDependencyError, dependency_error)
    app.add_exception_handler(QueueFullError, queue_error)
    app.add_exception_handler(RequiredLocalServiceError, local_service_error)
    return app
