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
HISTORICAL_V1 = {
    "methodology": "v1 substring source matching and top-8 only; historical, not comparable",
    "bm25_document_question_denominator": 17,
    "bm25_document_recall@8": 1.0,
    "bm25_document_mrr@8": 0.80203081232493,
    "bm25_document_ndcg@8": 0.8465798943800956,
    "bm25_section_recall@8": 0.5625,
    "bm25_section_mrr@8": 0.3757440476190476,
    "bm25_section_ndcg@8": 0.39747341484019283,
    "bm25_section_question_denominator": 16,
    "demo_required_facts": {"numerator": 32, "denominator": 32},
    "demo_document_citations": {"numerator": 17, "denominator": 17},
    "demo_section_citations": {"numerator": 17, "denominator": 21},
    "demo_outcomes": {"success": 17, "legal_refusal": 1, "technical_refusal": 0},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/organizers"))
    parser.add_argument("--assets-dir", type=Path, default=Path("submission/assets/bge_m3"))
    parser.add_argument("--annotations", type=Path, default=Path("submission/evaluation_annotations.json"))
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, default=Path("submission/evaluation_report.json"))
    args = parser.parse_args(argv)
    report = evaluate(args.data_dir, args.assets_dir, args.annotations, args.predictions)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    return 0


def evaluate(
    data_dir: Path,
    assets_dir: Path,
    annotations_path: Path,
    predictions_path: Path | None = None,
) -> dict[str, Any]:
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
    methodology_checks = _methodology_checks(annotations, chunks, label_issues)
    corpus = [f"{row['document']} {row.get('section', '')} {row.get('search_text', row['text'])}" for row in chunks]
    bm25 = bm25s.BM25()
    bm25.index(bm25s.tokenize(corpus, stopwords="ru"))
    query_tokens = bm25s.tokenize([questions[question_id] for question_id in ids], stopwords="ru")
    ranked, _scores = bm25.retrieve(query_tokens, k=24)
    predictions = (
        _read_csv(predictions_path, "answer")
        if predictions_path is not None and predictions_path.is_file()
        else None
    )
    if predictions is not None and sorted(predictions, key=int) != ids:
        raise ValueError("predictions must contain exactly the same 18 IDs")
    rows = []
    for position, question_id in enumerate(ids):
        annotation = annotations[question_id]
        ranking24 = [chunks[int(index)] for index in ranked[position] if int(index) >= 0]
        ranking8 = ranking24[:8]
        answer = outputs[question_id]
        demo_score = _score_candidate(answer, annotation, exact_documents=False)
        section_labels = _section_labels(annotation)
        rows.append(
            {
                "id": question_id,
                "history": bool(annotation.get("history")),
                "ambiguous_label": bool(annotation.get("ambiguous")),
                "expected_outcome": annotation["outcome"],
                "query_source": "original",
                "bm25_document": _retrieval_at_8_24(
                    ranking8,
                    ranking24,
                    annotation["documents"],
                    lambda chunk, label: chunk["document"] == label,
                ) if annotation["documents"] else None,
                "bm25_section": _retrieval_at_8_24(
                    ranking8,
                    ranking24,
                    section_labels,
                    lambda chunk, label: chunk["document"] == label[0]
                    and chunk.get("section") == label[1],
                ) if section_labels else None,
                "official_demo_output": demo_score,
            }
        )
        if predictions is not None:
            rows[-1]["solution_prediction"] = _score_candidate(
                predictions[question_id], annotation, exact_documents=True
            )
    summary = _summarize(rows, predictions is not None)
    comparison = _comparison(summary)
    return {
        "dataset": {
            "questions": len(ids),
            "documents": len({row["document"] for row in chunks}),
            "chunks": len(chunks),
            "demo_output_role": "candidate example answers, not hidden golden",
            "predictions": str(predictions_path) if predictions is not None else None,
            "label_issues": label_issues,
            "methodology_checks": methodology_checks,
            "ambiguous_question_ids": [row["id"] for row in rows if row["ambiguous_label"]],
        },
        "summary": summary,
        "comparison": comparison,
        "historical_v1": HISTORICAL_V1,
        "questions": rows,
        "unavailable": {
            "analysis.intent_accuracy": "N/A: no recorded Analysis responses under matched conditions",
            "analysis.entity_f1": "N/A: no recorded Analysis responses under matched conditions",
            "analysis.history_and_date_accuracy": "N/A: no recorded Analysis responses under matched conditions",
            "faiss": "N/A: query embeddings or a matching replay are not available offline",
            "fusion": "N/A: requires the unavailable FAISS query rankings",
            "qwen3": "N/A: no matching reranker replay or organizer API run",
            "analysis.retrieval_queries": "N/A: retrieval used original questions; no recorded Analysis queries",
            "generation.claim_precision": "N/A: CSV answers have no structured claims registry",
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
        relevant = [row for row in chunks if row["document"] in annotation["documents"]]
        if annotation["documents"] and not relevant:
            issues.append(f"{question_id}: expected document not found")
            continue
        for fact in annotation["facts"]:
            if fact["document"] not in annotation["documents"]:
                issues.append(f"{question_id}: fact source is not a labeled document: {fact['document']}")
                continue
            matching_sections = [
                row for row in chunks
                if row["document"] == fact["document"] and row.get("section", "") == fact["section"]
            ]
            if not matching_sections:
                issues.append(
                    f"{question_id}: exact fact source/section not found: "
                    f"{fact['document']}#{fact['section']}"
                )
                continue
            if not any(fact["text"].casefold() in row["text"].casefold() for row in matching_sections):
                issues.append(
                    f"{question_id}: fact not found in exact source provision: {fact['text']}"
                )
    return issues


def _methodology_checks(
    annotations: dict[str, dict], chunks: list[dict], label_issues: list[str]
) -> dict[str, bool]:
    labeled = next(row for row in annotations.values() if row["documents"] and row["sections"])
    target = (labeled["documents"][0], labeled["sections"][0])
    same_section_other_document = next(
        row for row in chunks
        if row.get("section") == target[1] and row["document"] != target[0]
    )
    def exact_matcher(chunk: dict, label: tuple[str, str]) -> bool:
        return chunk["document"] == label[0] and chunk.get("section") == label[1]
    overlap_metrics = _label_ranking_metrics(
        [
            {"document": target[0], "section": target[1]},
            {"document": target[0], "section": target[1]},
        ],
        [target],
        exact_matcher,
        8,
    )
    historical = annotations["5"]
    history_ids = {question_id for question_id, row in annotations.items() if row.get("history")}
    checks = {
        "fact_labels_resolve_to_exact_provisions": not label_issues,
        "same_section_in_other_document_is_rejected": not exact_matcher(
            same_section_other_document, target
        ),
        "overlapping_chunks_do_not_inflate_ndcg": overlap_metrics["ndcg"] == 1.0,
        "historical_revision_uses_exact_2023_source": (
            historical.get("date_sensitive") is True
            and len(historical["documents"]) == 1
            and "2023" in historical["documents"][0]
            and "2025" not in historical["documents"][0]
        ),
        "dialogue_questions_are_isolated_and_labeled": history_ids == {"6", "15", "16", "17", "18"},
    }
    if not all(checks.values()):
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise ValueError(f"evaluation methodology self-check failed: {failed}")
    return checks


def _retrieval_at_8_24(
    ranking8: list[dict], ranking24: list[dict], labels: list[Any], matcher
) -> dict[str, dict[str, float]]:
    return {
        "@8": _label_ranking_metrics(ranking8, labels, matcher, 8),
        "@24": _label_ranking_metrics(ranking24, labels, matcher, 24),
    }


def _label_ranking_metrics(
    ranking: list[dict], labels: list[Any], matcher, cutoff: int
) -> dict[str, float]:
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
    return {
        "recall": recall,
        "mrr": mrr,
        "ndcg": dcg / idcg,
        "cutoff": cutoff,
        "relevant_labels": len(labels),
    }


def _section_labels(annotation: dict) -> list[tuple[str, str]]:
    labels = []
    for fact in annotation["facts"]:
        label = (fact["document"], fact["section"])
        if fact["section"] and label not in labels:
            labels.append(label)
    return labels


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {"value": numerator / denominator if denominator else None, "numerator": numerator, "denominator": denominator}


def _looks_like_refusal(answer: str) -> bool:
    text = answer.casefold()
    return "нет документов" in text or "ответить по документам я не могу" in text


def _looks_like_technical_refusal(answer: str) -> bool:
    text = " ".join(answer.casefold().split()).rstrip(".")
    default_refusals = {
        "недостаточно подтверждённых данных для ответа",
        "недостаточно подтверждённых данных в предоставленных документах",
    }
    return text in default_refusals or any(
        marker in text
        for marker in (
            "техническая ошибка",
            "технический отказ",
            "technical error",
            "не удалось обработать",
        )
    )


def _aliases_for_document(document: str) -> tuple[str, ...]:
    for fragment, aliases in DOCUMENT_ALIASES.items():
        if fragment.casefold() in document.casefold():
            return aliases
    return (document,)


def _score_candidate(answer: str, annotation: dict, *, exact_documents: bool) -> dict[str, Any]:
    folded = answer.casefold()
    fact_hits = [fact["text"].casefold() in folded for fact in annotation["facts"]]
    document_hits = []
    for document in annotation["documents"]:
        candidates = (document,) if exact_documents else _aliases_for_document(document)
        document_hits.append(any(candidate.casefold() in folded for candidate in candidates))
    section_hits = [section.casefold() in folded for section in annotation["sections"]]
    if _looks_like_technical_refusal(answer):
        outcome = "technical_refusal"
    elif _looks_like_refusal(answer):
        outcome = "legal_refusal"
    else:
        outcome = "success"
    return {
        "required_fact_recall": _ratio(sum(fact_hits), len(fact_hits)),
        "citation_document_coverage": _ratio(sum(document_hits), len(document_hits)),
        "citation_section_coverage": _ratio(sum(section_hits), len(section_hits)),
        "outcome": outcome,
        "false_refusal": outcome == "legal_refusal" and annotation["outcome"] == "success",
        "missed_refusal": outcome != "legal_refusal" and annotation["outcome"] == "legal_refusal",
    }


def _summarize(rows: list[dict], predictions_available: bool) -> dict[str, Any]:
    document = [row["bm25_document"] for row in rows if row["bm25_document"] is not None]
    section = [row["bm25_section"] for row in rows if row["bm25_section"] is not None]
    demo = [row["official_demo_output"] for row in rows]
    summary = {
        "retrieval": {
            "query_source": "original",
            "local_bm25_document": _summarize_rankings(document),
            "local_bm25_section": _summarize_rankings(section),
            "analysis_query_rankings": {
                "available": False,
                "reason": "N/A: no recorded Analysis queries under matched conditions",
            },
        },
        "official_demo_output_observation": _summarize_candidates(demo),
    }
    if predictions_available:
        summary["solution_predictions"] = {
            "available": True,
            "generation_quality": {
                "available": False,
                "reason": "N/A: submission.csv has final free text, not the structured Generation claims",
            },
            "final_answer_quality": _summarize_candidates(
                [row["solution_prediction"] for row in rows]
            ),
        }
    else:
        summary["solution_predictions"] = {
            "available": False,
            "reason": "N/A: --predictions was not supplied or the file does not exist",
            "generation_quality": None,
            "final_answer_quality": None,
        }
    return summary


def _summarize_rankings(items: list[dict]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "question_denominator": len(items),
        "label_denominator": sum(item["@24"]["relevant_labels"] for item in items),
        "aggregation": "macro average over labeled questions",
    }
    for cutoff in ("@8", "@24"):
        result[cutoff] = {
            name: mean(item[cutoff][name] for item in items)
            for name in ("recall", "mrr", "ndcg")
        }
    return result


def _summarize_candidates(items: list[dict]) -> dict[str, Any]:
    facts = [item["required_fact_recall"] for item in items if item["required_fact_recall"]["denominator"]]
    citation_docs = [item["citation_document_coverage"] for item in items if item["citation_document_coverage"]["denominator"]]
    citation_sections = [item["citation_section_coverage"] for item in items if item["citation_section_coverage"]["denominator"]]
    return {
        "role": "candidate answer observation, not model quality or hidden-golden score",
        "required_fact_recall": _aggregate_ratios(facts),
        "citation_document_coverage": _aggregate_ratios(citation_docs),
        "citation_section_coverage": _aggregate_ratios(citation_sections),
        "false_refusal_rate": _ratio(sum(item["false_refusal"] for item in items), 17),
        "missed_refusal_rate": _ratio(sum(item["missed_refusal"] for item in items), 1),
        "technical_refusal_rate": _ratio(
            sum(item["outcome"] == "technical_refusal" for item in items), len(items)
        ),
        "outcomes": {
            outcome: sum(item["outcome"] == outcome for item in items)
            for outcome in ("success", "legal_refusal", "technical_refusal")
        },
    }


def _aggregate_ratios(items: list[dict]) -> dict[str, int | float | None]:
    numerator = sum(int(item["numerator"]) for item in items)
    denominator = sum(int(item["denominator"]) for item in items)
    return _ratio(numerator, denominator)


def _comparison(summary: dict[str, Any]) -> list[dict[str, Any]]:
    documents = summary["retrieval"]["local_bm25_document"]
    sections = summary["retrieval"]["local_bm25_section"]
    metrics = [
        (f"local_bm25.document.{name}{cutoff}", documents[cutoff][name], documents["question_denominator"])
        for cutoff in ("@8", "@24")
        for name in ("recall", "mrr", "ndcg")
    ] + [
        (f"local_bm25.section.{name}{cutoff}", sections[cutoff][name], sections["question_denominator"])
        for cutoff in ("@8", "@24")
        for name in ("recall", "mrr", "ndcg")
    ]
    return [
        {
            "metric": name,
            "value": value,
            "denominator": denominator,
            "before": None,
            "after": value,
            "change": None,
            "comparison_reason": "N/A: historical v1 used substring labels and a different methodology",
        }
        for name, value, denominator in metrics
    ]


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Offline RAG quality evaluation", "", "Official demo_output is observed as a candidate example only; its figures are not model-quality scores.", "", "## Summary", "", "```json", json.dumps(report["summary"], ensure_ascii=False, indent=2), "```", "", "## Current exact-label metrics", "", "| Metric | Value | Denominator | Historical comparison |", "|---|---:|---:|---|"]
    for item in report["comparison"]:
        lines.append(
            f"| {item['metric']} | {item['value']:.6f} | {item['denominator']} | N/A (method changed) |"
        )
    lines.extend(["", "## Historical v1 baseline (not comparable)", "", "```json", json.dumps(report["historical_v1"], ensure_ascii=False, indent=2), "```", "", "## Per question", "", "| ID | History | Query | Doc R@8 | Doc R@24 | Section R@8 | Section R@24 | Demo facts | Demo outcome | Prediction facts | Prediction outcome |", "|---:|:---:|---|---:|---:|---:|---:|---:|---|---:|---|"])
    for row in report["questions"]:
        document = row["bm25_document"]
        section = row["bm25_section"]
        demo = row["official_demo_output"]
        prediction = row.get("solution_prediction")
        lines.append(
            f"| {row['id']} | {'yes' if row['history'] else 'no'} | {row['query_source']} | "
            f"{_fmt_ranking(document, '@8')} | {_fmt_ranking(document, '@24')} | "
            f"{_fmt_ranking(section, '@8')} | {_fmt_ranking(section, '@24')} | "
            f"{_fmt_ratio(demo['required_fact_recall'])} | {demo['outcome']} | "
            f"{_fmt_ratio(prediction['required_fact_recall']) if prediction else 'N/A'} | "
            f"{prediction['outcome'] if prediction else 'N/A'} |"
        )
    lines.extend(["", "## Unavailable metrics", ""])
    lines.extend(f"- **{name}**: {reason}" for name, reason in report["unavailable"].items())
    return "\n".join(lines) + "\n"


def _fmt_ratio(value: dict) -> str:
    return "N/A" if value["value"] is None else f"{value['value']:.3f} ({value['numerator']}/{value['denominator']})"


def _fmt_ranking(value: dict | None, cutoff: str) -> str:
    return "N/A" if value is None else f"{value[cutoff]['recall']:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
