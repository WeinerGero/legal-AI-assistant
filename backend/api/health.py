"""Технические проверки состояния; не заменяют юридических метрик."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Процесс жив")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Готовность принимать пользовательские запросы")
def readyz() -> dict[str, str]:
    return {"status": "ok"}
