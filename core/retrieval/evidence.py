"""Отбор доказательств и присвоение ``evidence_id`` (ARCHITECTURE.md §7.6).

Правила:

- обязательные по реквизитам положения добавляются всегда;
- добавляются соседние пункты, определяющие срок, исключение или термин;
- применимые дополнительные соглашения и противоречащие положения;
- для каждого ``requested_fact`` проверяется достаточность набора;
- один ``evidence_id`` описывает один точный диапазон одной редакции.

Модуль работает только с ``ChunkRef`` и портом ``DocumentRepository``:
никаких обращений к БД, FAISS или клиентам моделей.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import re
from typing import Final
from uuid import UUID

from core.contracts import ClauseRange, ChunkRef, DocumentRef, DocumentRepository, Evidence

CLAUSE_HEADING: Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?:статья|раздел|глава|пункт|п\.|ст\.)?\s*\d+(?:\.\d+)*[.)]?\s+"
)

# Маркеры положений, которым нужен соседний пункт: срок, исключение, термин.
NEIGHBOR_MARKERS: Final[tuple[str, ...]] = (
    "срок",
    "в течение",
    "не позднее",
    "до истечения",
    "если иное",
    "за исключением",
    "исключение",
    "не применяется",
    "не распространяется",
    "для целей настоящего",
    "означает",
    "понимается",
    "определени",
    "датой поставки",
    "датой подписания",
)

# Маркеры положений, задающих иную редакцию правила.
CONTRADICTION_MARKERS: Final[tuple[str, ...]] = (
    "утратил силу",
    "не действует",
    "признан утратившим",
    "изложить в новой редакции",
    "исключить",
    "дополнить",
)

# Основа слова короче этого порога не даёт надёжного совпадения.
MIN_STEM_LENGTH: Final[int] = 3

# Минимальная доля совпавших основ, при которой факт считается покрытым.
# Проверка лексическая, поэтому полного совпадения всех словоформ
# в русском тексте ожидать нельзя.
MIN_FACT_COVERAGE_PERCENT: Final[int] = 60


@dataclass(slots=True)
class EvidenceDraft:
    """Кандидат в реестр до присвоения ``E###``."""

    chunk: ChunkRef
    document: DocumentRef
    clause_id: UUID
    text: str
    section: str
    reason: str
    start_char: int
    end_char: int
    applicability_note: str | None = None


@dataclass(slots=True)
class EvidenceSelection:
    """Результат отбора: реестр и диагностика достаточности."""

    evidence: list[Evidence]
    missing_facts: list[str]

    @property
    def sufficient(self) -> bool:
        return not self.missing_facts


def assign_evidence_ids(records: Sequence[Evidence]) -> list[Evidence]:
    """Перенумеровывает реестр в ``E001``, ``E002``… детерминированно."""
    return [
        item.model_copy(update={"evidence_id": f"E{position:03d}"})
        for position, item in enumerate(records, start=1)
    ]


def missing_facts(
    requested_facts: Sequence[str],
    covered_facts: Sequence[str],
) -> list[str]:
    """Возвращает запрошенные факты, для которых нет основания в наборе.

    Если вопрос требует одновременно срока и точки начала отсчёта,
    наличие только срока недостаточно.
    """
    covered = {_normalize(text) for text in covered_facts}
    return [fact for fact in requested_facts if _normalize(fact) not in covered]


def split_multi_clause_chunk(chunk: ChunkRef) -> list[UUID]:
    """Возвращает отдельные ``clause_id`` фрагмента, пересекающего границы.

    Модель не получает право ссылаться на весь многочастный фрагмент
    как на единый юридический пункт.
    """
    return list(chunk.clause_ids)


def is_contradicting(text: str) -> bool:
    """Признак положения, которое задаёт иную редакцию правила."""
    lowered = text.lower()
    return any(marker in lowered for marker in CONTRADICTION_MARKERS)


def needs_neighbor(text: str) -> bool:
    """Признак положения, которому нужен соседний пункт для полноты."""
    lowered = text.lower()
    return any(marker in lowered for marker in NEIGHBOR_MARKERS)


def covered_facts(records: Sequence[Evidence], requested_facts: Sequence[str]) -> list[str]:
    """Определяет, какие запрошенные факты подтверждаются текстом доказательств.

    Проверка лексическая и намеренно консервативная: совпадение основ
    значимых слов. Недостаточная уверенность трактуется как
    «факт не покрыт», а не наоборот.
    """
    if not records:
        return []
    tokens: set[str] = set()
    for record in records:
        tokens.update(_normalize(record.text).split())
    result: list[str] = []
    for fact in requested_facts:
        stems = [stem for stem in _fact_stems(fact) if len(stem) >= MIN_STEM_LENGTH]
        # Все значимые основы сразу не найти: в тексте они стоят в других
        # формах (провер- / проверить, качеств- / качества). Доля совпадений
        # отсекает факты, для которых оснований в наборе нет вовсе.
        matched = sum(1 for stem in stems if any(stem in token for token in tokens))
        if stems and matched * 100 >= len(stems) * MIN_FACT_COVERAGE_PERCENT:
            result.append(fact)
    return result


def build_evidence(
    *,
    documents: DocumentRepository,
    mandatory: Sequence[ChunkRef],
    ranked: Sequence[UUID],
    requested_facts: Sequence[str],
    applicability_notes: dict[UUID, str] | None = None,
    max_items: int = 20,
) -> EvidenceSelection:
    """Собирает реестр ``E###`` из обязательных и переранжированных фрагментов.

    Порядок фиксирован: сначала обязательные по реквизитам положения
    в порядке выдачи SQL, затем отобранные ранжированием. Так нумерация
    ``evidence_id`` воспроизводима при одинаковом входе.
    """
    notes = applicability_notes or {}
    ordered: list[ChunkRef] = []
    reasons: dict[UUID, str] = {}
    for chunk in mandatory:
        if chunk.chunk_id not in reasons:
            reasons[chunk.chunk_id] = "mandatory"
            ordered.append(chunk)
    for chunk_id in ranked:
        if len(ordered) >= max_items:
            break
        if chunk_id in reasons:
            continue
        reasons[chunk_id] = "ranked"
        ordered.append(documents.get_chunk(chunk_id))

    drafts: list[EvidenceDraft] = []
    for chunk in ordered:
        drafts.extend(
            _explode_chunk(
                documents=documents,
                chunk=chunk,
                note=notes.get(chunk.chunk_id),
                reason=reasons[chunk.chunk_id],
            )
        )
        if len(drafts) >= max_items:
            break
    # Один ``evidence_id`` — один точный диапазон: лимит применяется
    # к готовым диапазонам, а не к фрагментам до раскрытия по пунктам.
    drafts = drafts[:max_items]

    records = [
        Evidence(
            evidence_id=f"E{position:03d}",
            chunk_id=draft.chunk.chunk_id,
            document_version_id=draft.chunk.document_version_id,
            clause_id=draft.clause_id,
            document_title=draft.document.title,
            version_label=draft.document.version_label,
            section=draft.section,
            text=draft.text,
            applicability_note=draft.applicability_note,
        )
        for position, draft in enumerate(drafts, start=1)
    ]
    missing = missing_facts(requested_facts, covered_facts(records, requested_facts))
    return EvidenceSelection(evidence=records, missing_facts=missing)


def _explode_chunk(
    *,
    documents: DocumentRepository,
    chunk: ChunkRef,
    note: str | None,
    reason: str,
) -> list[EvidenceDraft]:
    """Раскрывает фрагмент по границам положений (§7.6).

    Один ``evidence_id`` описывает один точный диапазон одной редакции,
    поэтому фрагмент, пересекающий несколько пунктов, разбивается
    на отдельные диапазоны.
    """
    document = documents.get_document_version(chunk.document_version_id)
    ranges = _clause_ranges(document, chunk)
    if not ranges:
        return [
            EvidenceDraft(
                chunk=chunk,
                document=document,
                clause_id=_primary_clause(chunk),
                text=_slice(document, chunk),
                section=_section_label(document, chunk.start_char),
                reason=reason,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                applicability_note=note,
            )
        ]
    parts: list[EvidenceDraft] = []
    for clause in ranges:
        parts.append(
            EvidenceDraft(
                chunk=chunk,
                document=document,
                clause_id=clause.clause_id,
                text=document.canonical_text[clause.start_char : clause.end_char],
                section=clause.section or _section_label(document, clause.start_char),
                reason=reason,
                start_char=clause.start_char,
                end_char=clause.end_char,
                applicability_note=note,
            )
        )
    return parts


def _clause_ranges(document: DocumentRef, chunk: ChunkRef) -> list[ClauseRange]:
    """Возвращает сохранённые границы или восстанавливает их по заголовкам.

    Неоднозначный многопунктовый фрагмент отклоняется: равномерное деление
    создавало бы ложные цитаты и неверное соответствие ``clause_id``.
    """
    if chunk.clause_ranges:
        ranges = list(chunk.clause_ranges)
        expected_ids = tuple(chunk.clause_ids)
        actual_ids = tuple(item.clause_id for item in ranges)
        if expected_ids and actual_ids != expected_ids:
            raise ValueError("порядок clause_ranges не совпадает с clause_ids")
        previous_end = chunk.start_char
        for item in ranges:
            if not (
                chunk.start_char <= item.start_char < item.end_char <= chunk.end_char
            ):
                raise ValueError(f"границы пункта вне фрагмента: {item.clause_id}")
            if item.start_char < previous_end:
                raise ValueError(f"диапазоны пунктов пересекаются: {item.clause_id}")
            previous_end = item.end_char
        return ranges
    clauses = split_multi_clause_chunk(chunk)
    if len(clauses) <= 1:
        return []
    text = document.canonical_text[chunk.start_char : chunk.end_char]
    starts = [match.start() for match in CLAUSE_HEADING.finditer(text)]
    if len(starts) != len(clauses):
        raise ValueError(
            f"нет точных границ для {len(clauses)} пунктов фрагмента {chunk.chunk_id}"
        )
    return [
        ClauseRange(
            clause_id=clause_id,
            start_char=chunk.start_char + start,
            end_char=chunk.start_char + (
                starts[index + 1] if index + 1 < len(starts) else len(text)
            ),
        )
        for index, (clause_id, start) in enumerate(zip(clauses, starts, strict=True))
    ]


def _slice(document: DocumentRef, chunk: ChunkRef) -> str:
    text = document.canonical_text[chunk.start_char : chunk.end_char]
    if not text:
        return chunk.search_text
    return text


def _section_label(document: DocumentRef, position: int) -> str:
    """Метка пункта для человека; при неуверенности — без выдуманного номера."""
    line_start = document.canonical_text.rfind("\n", 0, position) + 1
    line = document.canonical_text[line_start:].split("\n", 1)[0].strip()
    lowered = line.lower()
    for marker in ("пункт", "п.", "статья", "ст.", "раздел", "глава", "строка"):
        if lowered.startswith(marker):
            return line[:80]
    return ""


def _primary_clause(chunk: ChunkRef) -> UUID:
    """Основное положение диапазона; фрагмент без связей получает свой id."""
    if chunk.clause_ids:
        return chunk.clause_ids[0]
    return chunk.chunk_id


def _normalize(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def _fact_stems(fact: str) -> Iterable[str]:
    for token in _normalize(fact).split():
        yield token[:-2] if len(token) >= 5 else token
