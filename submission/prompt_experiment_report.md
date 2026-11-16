# Prompt experiment report

The runtime still uses the baseline v2 prompts. Candidate prompts are not connected.

## Real offline measurements

| Metric | Value | Denominator / reason |
|---|---:|---|
| BM25 document Recall@8 | 1.000000 | 17 questions |
| BM25 document nDCG@8 | 0.846580 | 17 questions |
| BM25 section Recall@8 | 0.562500 | 16 questions / 21 labels |
| BM25 section nDCG@8 | 0.416634 | 16 questions |
| Candidate reranker / claims / verification / latency | N/A | No matched live or replayed model responses for baseline and candidate prompts. |

These BM25 figures use original questions and are shared context, not evidence that either prompt variant is better.

Candidate design: atomic requested facts and claims; explicit version/date/identifier checks; every verification claim checked once with unchanged evidence IDs and a nonempty reason.

## Baseline versus candidate

| Metric | Baseline | Candidate | Change |
|---|---:|---:|---:|
| reranker_recall@8 | N/A | N/A | N/A |
| reranker_ndcg@8 | N/A | N/A | N/A |
| claim_correctness | N/A | N/A | N/A |
| fact_completeness | N/A | N/A | N/A |
| verification_error_rate | N/A | N/A | N/A |
| refusal_rate | N/A | N/A | N/A |
| p50_ms | N/A | N/A | N/A |
| p95_ms | N/A | N/A | N/A |
| api_calls | N/A | N/A | N/A |

All variant comparisons are N/A because no matched live or replayed outputs exist.

## Simulated parser-contract checks

Passed: 5/5. These are fixtures, not LLM measurements.

| Case | Verdict | Statuses | Parser compatible |
|---|---|---|:---:|
| supported_answer | pass | supported | yes |
| wrong_revision | revision_required | contradicted | yes |
| partial_support | revision_required | partially_supported | yes |
| false_citation | revision_required | unsupported | yes |
| justified_refusal | pass | none | yes |

## API capability conclusion

`response_format=json_object`, `thinking`, `reasoning_effort`, and a separate reranker `instruction` remain disabled by default because organizer-proxy compatibility was not measured.

Decision: Keep v2 prompts and current reranker payload as runtime defaults until matched real or replayed measurements show improvement.
