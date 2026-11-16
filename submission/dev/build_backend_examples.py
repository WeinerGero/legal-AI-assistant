from __future__ import annotations

import json
import zipfile
from pathlib import Path

from core.contracts import RenderedAnswerV1


FIXED_TIME = (2024, 1, 1, 0, 0, 0)


def build(output: Path) -> Path:
    base = {
        "schema_version": "1.0",
        "answer_id": "example-001",
        "legal_status": "answered",
        "verification_status": "passed",
        "blocks": [],
        "sources": [],
        "missing_information": [],
        "clarification_question": None,
    }
    variants = {
        "full": {},
        "partial": {"verification_status": "partial", "missing_information": ["Не подтверждён срок"]},
        "historical": {"missing_information": ["Историческая редакция до указанной даты отсутствует"]},
        "contradictory": {"verification_status": "partial", "missing_information": ["Источники содержат неустранённое противоречие"]},
        "clarification": {"legal_status": "clarification_required", "verification_status": "partial", "clarification_question": "Укажите номер договора."},
        "refusal": {"legal_status": "insufficient_evidence", "verification_status": "failed", "missing_information": ["Отсутствуют применимые документы"]},
        "technical_error": {"legal_status": "insufficient_evidence", "verification_status": "technical_error", "missing_information": ["Модель временно недоступна"]},
        "corrected": {"answer_id": "example-corrected", "missing_information": []},
    }
    entries: dict[str, bytes] = {
        "rendered-answer.schema.json": json.dumps(
            RenderedAnswerV1.model_json_schema(), ensure_ascii=False, indent=2
        ).encode("utf-8")
    }
    for name, changes in variants.items():
        payload = RenderedAnswerV1.model_validate({**base, **changes}).model_dump(mode="json")
        entries[f"examples/{name}.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build(args.output))
