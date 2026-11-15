from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app import app as backend_app
from integration.config import IntegrationConfig
from integration.handler import RagHandler


def create_app(config: IntegrationConfig | None = None) -> FastAPI:
    """Attach the RAG explicitly while preserving the backend app and routes."""
    app = backend_app
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def integrated_lifespan(application: FastAPI):
        async with original_lifespan(application):
            handler = RagHandler(config or IntegrationConfig.from_env())
            application.state.core_handler = handler
            application.state.rag_ready = True
            try:
                yield
            finally:
                application.state.rag_ready = False
                application.state.core_handler = None
                handler.close()

    app.router.lifespan_context = integrated_lifespan

    if not any(getattr(route, "path", None) == "/integration/readyz" for route in app.routes):
        @app.get("/integration/readyz")
        def integration_ready() -> dict[str, bool]:
            return {"rag_ready": bool(getattr(app.state, "rag_ready", False))}
    return app
