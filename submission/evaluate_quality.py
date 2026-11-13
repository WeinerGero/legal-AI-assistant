"""Reproducible offline quality evaluation for the 18 organizer demo questions."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

import bm25s

DOCUMENT_ALIASES = {
    "Договор аренды А-08-2024": ("а-08/2024", "а-08-2024"),
    "Договор поставки П-17-2024": ("п-17/2024", "п-17-2024"),
    "Договор поставки ПТ-05-2023": ("пт-05/2023", "пт-05-2023"),
    "Регламент согласования договоров ред 2023": ("10.01.2023",),
    "Регламент согласования договоров ред 2025": ("01.03.2025",),
    "Служебная записка об архиве договоров": ("служебной записке", "юо-7"),
    "ТК_ст70": ("ст. 70", "статья 70"),
    "ТК_ст59": ("ст. 59", "статья 59"),
    "152ФЗ_ст9": ("152-фз", "ст. 9"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/organizers"))
    parser.add_argument("--assets-dir", type=Path, default=Path("submission/assets/bge_m3"))
    parser.add_argument("--annotations", type=Path, default=Path("submission/evaluation_annotations.json"))
    parser.add_argument("--output", type=Path, default=Path("submission/evaluation_report.json"))
    args = parser.parse_args(argv)
    report = evaluate(args.data_dir, args.assets_dir, args.annotations)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return 0


def evaluate(data_dir: Path, assets_dir: Path, annotations_path: Path) -> dict[str, Any]:
    questions = _read_csv(data_dir / "demo_questions.csv", "question")
    outputs = _read_csv(data_dir / "demo_output.csv", "answer")
    annotations_payload = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations = {row["id"]: row for row in annotations_payload["questions"]}
    chunks_payload = json.loads((assets_dir / "chunks.json").read_text(encoding="utf-8"))
    chunks = chunks_payload.get("chunks", []) if isinstance(chunks_payload, dict) else chunks_payload
    ids = sorted(questions, key=int)
    if ids != sorted(outputs, key=int) or ids != sorted(annotations, key=int) or len(ids) != 18:
        raise ValueError("evaluation requires the same 18 IDs in questions, outputs and annotations")
    label_issues = _validate_labels(annotations, chunks)
    corpus = [f"{row['document']} {row.get('section', '')} {row.get('search_text', row['text'])}" for row in chunks]
    bm25 = bm25s.BM25()
    bm25.index(bm25s.tokenize(corpus, stopwords="ru"))
    query_tokens = bm25s.tokenize([questions[question_id] for question_id in ids], stopwords="ru")
    ranked, _scores = bm25.retrieve(query_tokens, k=8)
    rows = []
    for position, question_id in enumerate(ids):
        annotation = annotations[question_id]
        ranking = [chunks[int(index)] for index in ranked[position] if int(index) >= 0]
        answer = outputs[question_id]
        fact_hits = [fact.casefold() in answer.casefold() for fact in annotation["facts"]]
        citation_hits = [
            any(alias.casefold() in answer.casefold() for alias in DOCUMENT_ALIASES.get(document, (document,)))
            for document in annotation["documents"]
        ]
        section_hits = [section.casefold() in answer.casefold() for section in annotation["sections"]]
        observed_outcome = "legal_refusal" if _looks_like_refusal(answer) else "success"
        rows.append(
            {
                "id": question_id,
                "history": bool(annotation.get("history")),
                "ambiguous_label": bool(annotation.get("ambiguous")),
                "expected_outcome": annotation["outcome"],
                "observed_demo_outcome": observed_outcome,
                "bm25_document": _label_ranking_metrics(
                    ranking,
                    annotation["documents"],
                    lambda chunk, label: label.casefold() in chunk["document"].casefold(),
                ) if annotation["documents"] else None,
                "bm25_section": _label_ranking_metrics(
                    ranking,
                    annotation["sections"],
                    lambda chunk, label: _document_relevant(chunk, annotation)
                    and label.casefold() in f"{chunk.get('section', '')} {chunk.get('text', '')}".casefold(),
                ) if annotation["sections"] else None,
                "required_fact_recall": _ratio(sum(fact_hits), len(fact_hits)),
                "citation_document_coverage": _ratio(sum(citation_hits), len(citation_hits)),
                "citation_section_coverage": _ratio(sum(section_hits), len(section_hits)),
                "false_refusal": observed_outcome == "legal_refusal" and annotation["outcome"] != "legal_refusal",
                "missed_refusal": observed_outcome != "legal_refusal" and annotation["outcome"] == "legal_refusal",
            }
        )
    summary = _summarize(rows)
    comparison = _comparison(summary)
    return {
        "dataset": {
            "questions": len(ids),
            "documents": len({row["document"] for row in chunks}),
            "chunks": len(chunks),
            "demo_output_role": "candidate example answers, not hidden golden",
            "label_issues": label_issues,
            "ambiguous_question_ids": [row["id"] for row in rows if row["ambiguous_label"]],
        },
        "summary": summary,
        "comparison": comparison,
        "questions": rows,
        "unavailable": {
            "analysis.intent_accuracy": "N/A: no recorded Analysis responses under matched conditions",
            "analysis.entity_f1": "N/A: no recorded Analysis responses under matched conditions",
            "analysis.history_and_date_accuracy": "N/A: no recorded Analysis responses under matched conditions",
            "faiss": "N/A: query embeddings or a matching replay are not available offline",
            "fusion": "N/A: requires the unavailable FAISS query rankings",
            "qwen3": "N/A: no matching reranker replay or organizer API run",
            "generation.claim_precision": "N/A: demo_output has no structured claims registry",
            "generation.hallucination_rate": "N/A: free-text claims were not independently exhaustively annotated",
            "verification.precision_recall": "N/A: no independently injected error set and verifier outputs",
            "runtime.p50_p95": "N/A: demo_output contains no matched runtime trace",
            "runtime.api_calls_retries": "N/A: demo_output contains no matched runtime trace",
            "comparison.before_after": "N/A: no previous responses produced from identical API replay",
            "history.recovery_quality": "N/A: questions 6 and 15-18 have no matched recovery trace",
        },
    }


def _read_csv(path: Path, value_field: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["id"]: row[value_field] for row in csv.DictReader(handle)}


def _validate_labels(annotations: dict[str, dict], chunks: list[dict]) -> list[str]:
    issues = []
    for question_id, annotation in annotations.items():
        relevant = [
            row for row in chunks
            if any(document.casefold() in row["document"].casefold() for document in annotation["documents"])
        ]
        if annotation["documents"] and not relevant:
            issues.append(f"{question_id}: expected document not found")
            continue
        source_text = " ".join(row.get("search_text", row["text"]) for row in relevant).casefold()
        for fact in annotation["facts"]:
            if fact.casefold() not in source_text:
                issues.append(f"{question_id}: fact not found in labeled documents: {fact}")
    return issues


def _document_relevant(chunk: dict, annotation: dict) -> bool:
    return any(document.casefold() in chunk["document"].casefold() for document in annotation["documents"])


def _label_ranking_metrics(ranking: list[dict], labels: list[str], matcher) -> dict[str, float]:
    hit_ranks = []
    for label in labels:
        rank = next(
            (index + 1 for index, chunk in enumerate(ranking) if matcher(chunk, label)),
            None,
        )
        if rank is not None:
            hit_ranks.append(rank)
    recall = len(hit_ranks) / len(labels)
    mrr = 1.0 / min(hit_ranks) if hit_ranks else 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank in hit_ranks)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, len(labels) + 1))
    return {"recall@8": recall, "mrr@8": mrr, "ndcg@8": dcg / idcg}


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"value": numerator / denominator if denominator else None, "numerator": numerator, "denominator": denominator}


def _looks_like_refusal(answer: str) -> bool:
    text = answer.casefold()
    return "нет документов" in text or "ответить по документам я не могу" in text


def _summarize(rows: list[dict]) -> dict[str, Any]:
    document = [row["bm25_document"] for row in rows if row["bm25_document"] is not None]
    section = [row["bm25_section"] for row in rows if row["bm25_section"] is not None]
    facts = [row["required_fact_recall"] for row in rows if row["required_fact_recall"]["denominator"]]
    citation_docs = [row["citation_document_coverage"] for row in rows if row["citation_document_coverage"]["denominator"]]
    citation_sections = [row["citation_section_coverage"] for row in rows if row["citation_section_coverage"]["denominator"]]
    return {
        "bm25_document": {name: mean(item[name] for item in document) for name in ("recall@8", "mrr@8", "ndcg@8")},
        "bm25_document_denominator": len(document),
        "bm25_section": {name: mean(item[name] for item in section) for name in ("recall@8", "mrr@8", "ndcg@8")},
        "bm25_section_denominator": len(section),
        "demo_required_fact_recall": _aggregate_ratios(facts),
        "demo_citation_document_coverage": _aggregate_ratios(citation_docs),
        "demo_citation_section_coverage": _aggregate_ratios(citation_sections),
        "demo_false_refusal_rate": _ratio(sum(row["false_refusal"] for row in rows), sum(row["expected_outcome"] != "legal_refusal" for row in rows)),
        "demo_missed_refusal_rate": _ratio(sum(row["missed_refusal"] for row in rows), sum(row["expected_outcome"] == "legal_refusal" for row in rows)),
        "demo_outcomes": {
            outcome: sum(row["observed_demo_outcome"] == outcome for row in rows)
            for outcome in ("success", "legal_refusal", "technical_refusal")
        },
        "history_subset_required_fact_recall": _aggregate_ratios([row["required_fact_recall"] for row in rows if row["history"] and row["required_fact_recall"]["denominator"]]),
    }


def _aggregate_ratios(items: list[dict]) -> dict[str, int | float | None]:
    numerator = sum(int(item["numerator"]) for item in items)
    denominator = sum(int(item["denominator"]) for item in items)
    return _ratio(numerator, denominator)


def _comparison(summary: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = [
        ("bm25_document.recall@8", summary["bm25_document"]["recall@8"], summary["bm25_document_denominator"]),
        ("bm25_document.mrr@8", summary["bm25_document"]["mrr@8"], summary["bm25_document_denominator"]),
        ("bm25_document.ndcg@8", summary["bm25_document"]["ndcg@8"], summary["bm25_document_denominator"]),
        ("bm25_section.recall@8", summary["bm25_section"]["recall@8"], summary["bm25_section_denominator"]),
        ("demo.required_fact_recall", summary["demo_required_fact_recall"]["value"], summary["demo_required_fact_recall"]["denominator"]),
        ("demo.citation_document_coverage", summary["demo_citation_document_coverage"]["value"], summary["demo_citation_document_coverage"]["denominator"]),
        ("demo.citation_section_coverage", summary["demo_citation_section_coverage"]["value"], summary["demo_citation_section_coverage"]["denominator"]),
    ]
    return [
        {
            "metric": name,
            "value": value,
            "denominator": denominator,
            "before": None,
            "after": value,
            "change": None,
            "comparison_reason": "N/A: no previous run under identical API replay",
        }
        for name, value, denominator in metrics
    ]


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Offline RAG quality evaluation", "", "Demo output is a candidate example, not a hidden golden.", "", "## Summary", "", "```json", json.dumps(report["summary"], ensure_ascii=False, indent=2), "```", "", "## Comparable metrics", "", "| Metric | Value | Denominator | Before | After | Change |", "|---|---:|---:|---:|---:|---:|"]
    for item in report["comparison"]:
        lines.append(
            f"| {item['metric']} | {item['value']:.6f} | {item['denominator']} | N/A | "
            f"{item['after']:.6f} | N/A |"
        )
    lines.extend(["", "## Per question", "", "| ID | History | BM25 doc R@8 | MRR@8 | nDCG@8 | BM25 section R@8 | Fact recall | Doc citation | Section citation | Outcome |", "|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---|"])
    for row in report["questions"]:
        document = row["bm25_document"]
        section = row["bm25_section"]
        document_recall = f"{document['recall@8']:.3f}" if document else "N/A"
        document_mrr = f"{document['mrr@8']:.3f}" if document else "N/A"
        document_ndcg = f"{document['ndcg@8']:.3f}" if document else "N/A"
        section_value = f"{section['recall@8']:.3f}" if section else "N/A"
        lines.append(
            f"| {row['id']} | {'yes' if row['history'] else 'no'} | {document_recall} | "
            f"{document_mrr} | {document_ndcg} | "
            f"{section_value} | {_fmt_ratio(row['required_fact_recall'])} | "
            f"{_fmt_ratio(row['citation_document_coverage'])} | "
            f"{_fmt_ratio(row['citation_section_coverage'])} | {row['observed_demo_outcome']} |"
        )
    lines.extend(["", "## Unavailable metrics", ""])
    lines.extend(f"- **{name}**: {reason}" for name, reason in report["unavailable"].items())
    return "\n".join(lines) + "\n"


def _fmt_ratio(value: dict) -> str:
    return "N/A" if value["value"] is None else f"{value['value']:.3f} ({value['numerator']}/{value['denominator']})"


if __name__ == "__main__":
    raise SystemExit(main())
