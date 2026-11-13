# Offline RAG quality evaluation

Demo output is a candidate example, not a hidden golden.

## Summary

```json
{
  "bm25_document": {
    "recall@8": 1.0,
    "mrr@8": 0.80203081232493,
    "ndcg@8": 0.8465798943800956
  },
  "bm25_document_denominator": 17,
  "bm25_section": {
    "recall@8": 0.5625,
    "mrr@8": 0.3757440476190476,
    "ndcg@8": 0.39747341484019283
  },
  "bm25_section_denominator": 16,
  "demo_required_fact_recall": {
    "value": 1.0,
    "numerator": 32,
    "denominator": 32
  },
  "demo_citation_document_coverage": {
    "value": 1.0,
    "numerator": 17,
    "denominator": 17
  },
  "demo_citation_section_coverage": {
    "value": 0.8095238095238095,
    "numerator": 17,
    "denominator": 21
  },
  "demo_false_refusal_rate": {
    "value": 0.0,
    "numerator": 0,
    "denominator": 17
  },
  "demo_missed_refusal_rate": {
    "value": 0.0,
    "numerator": 0,
    "denominator": 1
  },
  "demo_outcomes": {
    "success": 17,
    "legal_refusal": 1,
    "technical_refusal": 0
  },
  "history_subset_required_fact_recall": {
    "value": 1.0,
    "numerator": 10,
    "denominator": 10
  }
}
```

## Comparable metrics

| Metric | Value | Denominator | Before | After | Change |
|---|---:|---:|---:|---:|---:|
| bm25_document.recall@8 | 1.000000 | 17 | N/A | 1.000000 | N/A |
| bm25_document.mrr@8 | 0.802031 | 17 | N/A | 0.802031 | N/A |
| bm25_document.ndcg@8 | 0.846580 | 17 | N/A | 0.846580 | N/A |
| bm25_section.recall@8 | 0.562500 | 16 | N/A | 0.562500 | N/A |
| demo.required_fact_recall | 1.000000 | 32 | N/A | 1.000000 | N/A |
| demo.citation_document_coverage | 1.000000 | 17 | N/A | 1.000000 | N/A |
| demo.citation_section_coverage | 0.809524 | 21 | N/A | 0.809524 | N/A |

## Per question

| ID | History | BM25 doc R@8 | MRR@8 | nDCG@8 | BM25 section R@8 | Fact recall | Doc citation | Section citation | Outcome |
|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | no | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (2/2) | success |
| 2 | no | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (1/1) | 1.000 (1/1) | 0.000 (0/1) | success |
| 3 | no | N/A | N/A | N/A | N/A | N/A | N/A | N/A | legal_refusal |
| 4 | no | 1.000 | 1.000 | 1.000 | 0.500 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (2/2) | success |
| 5 | no | 1.000 | 0.125 | 0.315 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 6 | yes | 1.000 | 1.000 | 1.000 | 0.500 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (2/2) | success |
| 7 | no | 1.000 | 0.167 | 0.356 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 8 | no | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 9 | no | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (2/2) | success |
| 10 | no | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (1/1) | 1.000 (1/1) | 1.000 (1/1) | success |
| 11 | no | 1.000 | 0.200 | 0.387 | N/A | 1.000 (2/2) | 1.000 (1/1) | N/A | success |
| 12 | no | 1.000 | 0.143 | 0.333 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 0.000 (0/1) | success |
| 13 | no | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 0.500 (1/2) | success |
| 14 | no | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 0.000 (0/1) | success |
| 15 | yes | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 16 | yes | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 17 | yes | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |
| 18 | yes | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (2/2) | 1.000 (1/1) | 1.000 (1/1) | success |

## Unavailable metrics

- **analysis.intent_accuracy**: N/A: no recorded Analysis responses under matched conditions
- **analysis.entity_f1**: N/A: no recorded Analysis responses under matched conditions
- **analysis.history_and_date_accuracy**: N/A: no recorded Analysis responses under matched conditions
- **faiss**: N/A: query embeddings or a matching replay are not available offline
- **fusion**: N/A: requires the unavailable FAISS query rankings
- **qwen3**: N/A: no matching reranker replay or organizer API run
- **generation.claim_precision**: N/A: demo_output has no structured claims registry
- **generation.hallucination_rate**: N/A: free-text claims were not independently exhaustively annotated
- **verification.precision_recall**: N/A: no independently injected error set and verifier outputs
- **runtime.p50_p95**: N/A: demo_output contains no matched runtime trace
- **runtime.api_calls_retries**: N/A: demo_output contains no matched runtime trace
- **comparison.before_after**: N/A: no previous responses produced from identical API replay
- **history.recovery_quality**: N/A: questions 6 and 15-18 have no matched recovery trace
