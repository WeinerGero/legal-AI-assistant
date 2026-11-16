"""Replay-only RRF comparison; never calls a model or generates an answer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from submission.evaluate_golden import _load_relevance_labels
from submission.evaluate_quality import labeled_ranking_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    events = [json.loads(line) for line in args.events.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = {str(event.get("question_id")) for event in events}
    labels = _load_relevance_labels(ids)
    first = {}
    for event in events:
        if event.get("kind") == "rag":
            first.setdefault((str(event.get("question_id")), str(event.get("stage"))), event)
    rows = []
    for name, bm_weight, faiss_weight, diverse in (
        ("FAISS-only", 0.0, 1.0, False),
        ("RRF-0.5/0.5", 0.5, 0.5, False),
        ("RRF-0.25/0.75", 0.25, 0.75, False),
        ("RRF-0.1/0.9", 0.1, 0.9, False),
        ("Diverse", 0.5, 0.5, True),
    ):
        for cutoff in (8, 24):
            metrics = []
            required_hits = required_total = 0
            for question_id, annotation in labels.items():
                bm = list(first.get((question_id, "bm25"), {}).get("ranked_fragment_ids", []))
                faiss = list(first.get((question_id, "faiss"), {}).get("ranked_fragment_ids", []))
                if diverse:
                    ranking = []
                    for position in range(max(len(bm), len(faiss))):
                        for channel in (bm, faiss):
                            if position < len(channel) and channel[position] not in ranking:
                                ranking.append(channel[position])
                else:
                    scores = {}
                    for weight, channel in ((bm_weight, bm), (faiss_weight, faiss)):
                        for rank, item in enumerate(channel, 1):
                            scores[item] = scores.get(item, 0.0) + weight / (60 + rank)
                    ranking = sorted(scores, key=lambda item: (-scores[item], item))
                relevance = {row["trace_id"]: int(row["relevance"]) for row in annotation["fragments"]}
                metrics.append(labeled_ranking_metrics(ranking, relevance, cutoff))
                required = {row["trace_id"] for row in annotation["fragments"] if row.get("necessity") == "required"}
                required_hits += len(required & set(ranking[:cutoff]))
                required_total += len(required)
            if metrics:
                rows.append({"variant": name, "k": cutoff, "fragment_recall": sum(x["recall"] for x in metrics) / len(metrics), "ndcg": sum(x["ndcg"] for x in metrics) / len(metrics), "required_evidence_coverage": required_hits / required_total if required_total else None})
    args.output.write_text(json.dumps({"methodology": "Replay of saved flattened BM25/FAISS rankings; exact per-query channel lists were not persisted in P03, so this is diagnostic and not a replacement for live RRF metrics.", "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
