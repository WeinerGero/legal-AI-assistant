"""Точка входа FastAPI (ARCHITECTURE.md §11).

Маршруты имеют префикс ``/api/v1``, кроме технических проверок состояния.
Технические ошибки никогда не превращаются в ``legal_status:
insufficient_evidence``.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.api import admin, answers, auth, conversations, health


def create_app() -> FastAPI:
    """Сборка приложения без побочных эффектов при импорте."""
    app = FastAPI(
        title="ПСБ. Юридический ассистент",
        version="1.0.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(answers.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1/admin")
    return app


app = create_app()
