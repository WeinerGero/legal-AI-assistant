"""Streamlit-приложение (ARCHITECTURE.md §12).

Интерфейс только отображает структурированный ответ и открывает точный
источник по ``answer_source_id``; содержание текста не переписывается.
Полная история и канонические документы в ``st.session_state`` не копируются.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SessionView:
    """Минимальное состояние экрана, допустимое в ``st.session_state``."""

    conversation_id: str | None = None
    selected_source_id: str | None = None
    pending_request_id: str | None = None
    display_state: dict[str, object] = field(default_factory=dict)


BLOCK_LABELS: dict[str, str] = {
    "conclusion": "Вывод",
    "justification": "Обоснование",
    "qualification": "Условия и ограничения",
    "clarification": "Уточнение",
    "refusal": "Причина отказа",
}


def main() -> None:
    """Точка входа: экраны входа, чата, панели источника и администрирования."""
    raise NotImplementedError("streamlit-разметка экранов §12.1")
