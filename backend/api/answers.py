"""Ответы и точный просмотр источников (ARCHITECTURE.md §11.3).

Ответ отдаётся в формате ``RenderedAnswerV1``, источник — по сохранённой
редакции, а не по последней версии документа.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/answers", tags=["answers"])


@router.get("/{answer_id}")
def get_answer(answer_id: str) -> dict:
    raise NotImplementedError("backend.api.answers.service.rendered")


@router.get("/{answer_id}/sources/{answer_source_id}")
def get_answer_source(answer_id: str, answer_source_id: str) -> dict:
    """Документ, редакция и точный диапазон с проверкой права владельца."""
    raise NotImplementedError("backend.api.answers.service.source")
