# Offline RAG quality evaluation

Official demo_output is observed as a candidate example only; its figures are not model-quality scores.

## Summary

```json
{
  "retrieval": {
    "query_source": "original",
    "local_bm25_document": {
      "question_denominator": 17,
      "label_denominator": 17,
      "aggregation": "macro average over labeled questions",
      "@8": {
        "recall": 1.0,
        "mrr": 0.80203081232493,
        "ndcg": 0.8465798943800956
      },
      "@24": {
        "recall": 1.0,
        "mrr": 0.80203081232493,
        "ndcg": 0.8465798943800956
      }
    },
    "local_bm25_section": {
      "question_denominator": 16,
      "label_denominator": 21,
      "aggregation": "macro average over labeled questions",
      "@8": {
        "recall": 0.5625,
        "mrr": 0.4174107142857143,
        "ndcg": 0.4166342646141134
      },
      "@24": {
        "recall": 0.78125,
        "mrr": 0.43116544913419913,
        "ndcg": 0.475681641372779
      }
    },
    "analysis_query_rankings": {
      "available": false,
      "reason": "N/A: no recorded Analysis queries under matched conditions"
    }
  },
  "official_demo_output_observation": {
    "role": "candidate answer observation, not model quality or hidden-golden score",
    "required_fact_recall": {
      "value": 1.0,
      "numerator": 32,
      "denominator": 32
    },
    "citation_document_coverage": {
      "value": 1.0,
      "numerator": 17,
      "denominator": 17
    },
    "citation_section_coverage": {
      "value": 0.8095238095238095,
      "numerator": 17,
      "denominator": 21
    },
    "false_refusal_rate": {
      "value": 0.0,
      "numerator": 0,
      "denominator": 17
    },
    "missed_refusal_rate": {
      "value": 0.0,
      "numerator": 0,
      "denominator": 1
    },
    "outcomes": {
      "success": 17,
      "legal_refusal": 1,
      "technical_refusal": 0
    }
  },
  "solution_predictions": {
    "available": false,
    "reason": "N/A: --predictions was not supplied or the file does not exist",
    "generation_quality": null,
    "final_answer_quality": null
  }
}
```

## Current exact-label metrics

| Metric | Value | Denominator | Historical comparison |
|---|---:|---:|---|
| local_bm25.document.recall@8 | 1.000000 | 17 | N/A (method changed) |
| local_bm25.document.mrr@8 | 0.802031 | 17 | N/A (method changed) |
| local_bm25.document.ndcg@8 | 0.846580 | 17 | N/A (method changed) |
| local_bm25.document.recall@24 | 1.000000 | 17 | N/A (method changed) |
| local_bm25.document.mrr@24 | 0.802031 | 17 | N/A (method changed) |
| local_bm25.document.ndcg@24 | 0.846580 | 17 | N/A (method changed) |
| local_bm25.section.recall@8 | 0.562500 | 16 | N/A (method changed) |
| local_bm25.section.mrr@8 | 0.417411 | 16 | N/A (method changed) |
| local_bm25.section.ndcg@8 | 0.416634 | 16 | N/A (method changed) |
| local_bm25.section.recall@24 | 0.781250 | 16 | N/A (method changed) |
| local_bm25.section.mrr@24 | 0.431165 | 16 | N/A (method changed) |
| local_bm25.section.ndcg@24 | 0.475682 | 16 | N/A (method changed) |

## Historical v1 baseline (not comparable)

```json
{
  "methodology": "v1 substring source matching and top-8 only; historical, not comparable",
  "bm25_document_question_denominator": 17,
  "bm25_document_recall@8": 1.0,
  "bm25_document_mrr@8": 0.80203081232493,
  "bm25_document_ndcg@8": 0.8465798943800956,
  "bm25_section_recall@8": 0.5625,
  "bm25_section_mrr@8": 0.3757440476190476,
  "bm25_section_ndcg@8": 0.39747341484019283,
  "bm25_section_question_denominator": 16,
  "demo_required_facts": {
    "numerator": 32,
    "denominator": 32
  },
  "demo_document_citations": {
    "numerator": 17,
    "denominator": 17
  },
  "demo_section_citations": {
    "numerator": 17,
    "denominator": 21
  },
  "demo_outcomes": {
    "success": 17,
    "legal_refusal": 1,
    "technical_refusal": 0
  }
}
```

## Per question

| ID | History | Query | Doc R@8 | Doc R@24 | Section R@8 | Section R@24 | Demo facts | Demo outcome | Prediction facts | Prediction outcome |
|---:|:---:|---|---:|---:|---:|---:|---:|---|---:|---|
| 1 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 2 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (1/1) | success | N/A | N/A |
| 3 | no | original | N/A | N/A | N/A | N/A | N/A | legal_refusal | N/A | N/A |
| 4 | no | original | 1.000 | 1.000 | 0.500 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 5 | no | original | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 (2/2) | success | N/A | N/A |
| 6 | yes | original | 1.000 | 1.000 | 0.500 | 0.500 | 1.000 (2/2) | success | N/A | N/A |
| 7 | no | original | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 (2/2) | success | N/A | N/A |
| 8 | no | original | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 9 | no | original | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 10 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (1/1) | success | N/A | N/A |
| 11 | no | original | 1.000 | 1.000 | N/A | N/A | 1.000 (2/2) | success | N/A | N/A |
| 12 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 13 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 14 | no | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 15 | yes | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 16 | yes | original | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 (2/2) | success | N/A | N/A |
| 17 | yes | original | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |
| 18 | yes | original | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | success | N/A | N/A |

## Unavailable metrics

- **analysis.intent_accuracy**: N/A: no recorded Analysis responses under matched conditions
- **analysis.entity_f1**: N/A: no recorded Analysis responses under matched conditions
- **analysis.history_and_date_accuracy**: N/A: no recorded Analysis responses under matched conditions
- **faiss**: N/A: query embeddings or a matching replay are not available offline
- **fusion**: N/A: requires the unavailable FAISS query rankings
- **qwen3**: N/A: no matching reranker replay or organizer API run
- **analysis.retrieval_queries**: N/A: retrieval used original questions; no recorded Analysis queries
- **generation.claim_precision**: N/A: CSV answers have no structured claims registry
- **generation.hallucination_rate**: N/A: free-text claims were not independently exhaustively annotated
- **verification.precision_recall**: N/A: no independently injected error set and verifier outputs
- **runtime.p50_p95**: N/A: demo_output contains no matched runtime trace
- **runtime.api_calls_retries**: N/A: demo_output contains no matched runtime trace
- **comparison.before_after**: N/A: no previous responses produced from identical API replay
- **history.recovery_quality**: N/A: questions 6 and 15-18 have no matched recovery trace
