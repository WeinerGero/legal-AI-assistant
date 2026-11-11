"""Административные маршруты (ARCHITECTURE.md §11.4).

Загрузка документов и индексация доступны только ``admin`` и не изменяют
запечатанный конкурсный корпус.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["admin"])


@router.get("/users")
def list_users() -> dict:
    raise NotImplementedError("backend.api.admin.service.users")


@router.post("/users", status_code=201)
def create_user() -> dict:
    raise NotImplementedError("backend.api.admin.service.create_user")


@router.patch("/users/{user_id}")
def update_user(user_id: str) -> dict:
    raise NotImplementedError("backend.api.admin.service.update_user")


@router.get("/corpora")
def list_corpora() -> dict:
    raise NotImplementedError("backend.api.admin.service.corpora")


@router.get("/documents")
def list_documents() -> dict:
    raise NotImplementedError("backend.api.admin.service.documents")


@router.post("/documents", status_code=201)
def upload_document() -> dict:
    """Загрузка в демонстрационный корпус; конкурсный корпус запечатан."""
    raise NotImplementedError("backend.api.admin.service.upload_document")


@router.post("/documents/{document_id}/versions", status_code=201)
def upload_version(document_id: str) -> dict:
    raise NotImplementedError("backend.api.admin.service.upload_version")


@router.post("/index-jobs", status_code=202)
def start_index_job() -> dict:
    raise NotImplementedError("backend.api.admin.service.start_index_job")


@router.get("/index-jobs/{job_id}")
def get_index_job(job_id: str) -> dict:
    raise NotImplementedError("backend.api.admin.service.index_job")


@router.get("/metrics")
def metrics() -> dict:
    """Агрегированные технические метрики без пользовательского текста."""
    raise NotImplementedError("backend.api.admin.service.metrics")
