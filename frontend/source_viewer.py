"""HTML-компонент точного просмотра источника (ARCHITECTURE.md §10.4).

Получает экранированный канонический текст и диапазон подсветки.
Элементы документа маркируются ``data-clause-id``; прокрутка выполняется
только внутри изолированного компонента, но не из Markdown.
"""

from __future__ import annotations

from html import escape

HIGHLIGHT_STYLE = "background-color: #ffe58f; border-radius: 2px;"


def render_source_document(
    *,
    title: str,
    version_label: str,
    canonical_text: str,
    start_char: int,
    end_char: int,
    clause_id: str,
) -> str:
    """Возвращает безопасный HTML с выделенным участком исходного документа."""
    if not 0 <= start_char < end_char <= len(canonical_text):
        raise ValueError("диапазон вне канонического текста")

    before = escape(canonical_text[:start_char])
    target = escape(canonical_text[start_char:end_char])
    after = escape(canonical_text[end_char:])

    return (
        f"<section data-clause-id='{escape(clause_id)}'>"
        f"<header><strong>{escape(title)}</strong> · редакция {escape(version_label)}</header>"
        f"<article>{before}"
        f"<mark style=\"{HIGHLIGHT_STYLE}\">{target}</mark>"
        f"{after}</article>"
        "</section>"
    )
