# Component-only RAG quality report

This report deliberately excludes Codex legal-answer scores, DeepSeek judging,
new Golden runs, and answer generation. P01–P03 artifacts remain unchanged.

## Retrieval comparison on saved P03 traces

The replay uses saved flattened BM25/FAISS rankings and independent P03
provision labels. Exact per-query channel rankings were not persisted in the
original P03 trace, so these rows are diagnostic and are not substituted for
the live architecture metrics.

| Variant | Fragment Recall@8 | Fragment Recall@24 | nDCG@24 | Required Evidence@24 |
|---|---:|---:|---:|---:|
| FAISS-only | 66.7% | 73.3% | 0.647 | 7/8 |
| Existing RRF 0.5/0.5 | 56.7% | 66.7% | 0.622 | 6/8 |
| Weighted RRF 0.25/0.75 | 56.7% | 73.3% | 0.624 | 7/8 |
| Weighted RRF 0.1/0.9 | 66.7% | 73.3% | 0.632 | 7/8 |
| Diverse interleave | 66.7% | 73.3% | 0.592 | 7/8 |

The diagnostic replay does not demonstrate a strict quality improvement over
FAISS-only. Therefore no aggressive RRF weighting or diversity mode is enabled
by default. The runtime supports `RRF_MODE=faiss|weighted|diverse` and explicit
`RRF_BM25_WEIGHT`/`RRF_FAISS_WEIGHT`; default weighted 0.5/0.5 preserves the
existing behavior and exact-entity safeguards.

## Evidence selection

The runtime now performs deterministic greedy marginal coverage over the facts
extracted by DeepSeek #1, with rank as the tie-breaker. This is a candidate
selection heuristic, not legal support and not an independent semantic label.
The verifier remains authoritative. It preserves the evidence budget and
deduplicates records. P03 saved traces showed no loss after the Qwen3-to-
evidence boundary: **0/6**.

## Analysis, generation, verification and recovery

- Analysis keeps conversation context, dates, editions, exceptions, atomic
  facts and retrieval queries in the existing schema; no extra mandatory LLM
  call was added.
- Recovery now re-runs the existing analysis role using the full conversation,
  reconstructs fact requirements, performs addressable search, and verifies the
  result with those facts. It no longer searches only the final user turn with
  an empty fact list.
- Controlled verifier set: 8 deterministic structural cases, containing both
  valid and invalid verdicts. It is independent of DeepSeek and Codex.
- Legal-answer quality is intentionally **N/A** here; this task does not score
  answers.

## Rejected or not enabled

- FAISS-only was not made the production default: the P03 sample is too small
  to establish that exact lexical identifiers and contract discrimination do not
  regress.
- Weighted 0.1/0.9 was not made default: it matches FAISS recall in the replay
  but has lower nDCG than FAISS-only and the replay lacks exact per-query lists.
- No additional Qwen3 call was added in competition mode.
- No new model, dependency, Golden run, Codex evaluation, or DeepSeek judge was
  introduced.
