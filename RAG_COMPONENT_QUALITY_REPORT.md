# RAG component quality report — P03

HEAD: `6e906a3e1146ed9323094ade3814874597e71a8a` (code under test)  
Run: `replay-d97d552-p03`  
Cases: H3, P2, R5, C3, Fx2. Historical P03 was not overwritten.

The real P03 replay completed 5/5 cases using DeepSeek №1–3, BGE-M3/FAISS, BM25 and local Qwen3-Reranker-4B. No judge model or fourth LLM was used. Recalculation was then run from saved events only and produced the same CSV rows.

## Retrieval metrics (P03 independent annotation v1)

Values are macro over 5 annotated questions unless marked micro. Numerators/denominators are retained in `architecture_metrics.csv`.

| Component | Metric | K | Result |
|---|---|---:|---:|
| BM25 | Section Recall | 24 | 3.1667/5 = 63.33% |
| BM25 | Fragment Recall | 24 | 2.8333/5 = 56.67% |
| FAISS | Section Recall | 24 | 4.6667/5 = 93.33% |
| FAISS | Fragment Recall | 24 | 4.6667/5 = 93.33% |
| FAISS | Required Evidence Coverage | 24 | 8/8 = 100% |
| RRF | Section Recall | 24 | 4.3333/5 = 86.67% |
| RRF | Fragment Recall | 24 | 4.3333/5 = 86.67% |
| RRF | Required Evidence Coverage | 24 | 7/8 = 87.50% |
| Qwen3-4B | Section Recall | 8 | 4.0000/5 = 80.00% |
| Qwen3-4B | Fragment Recall | 8 | 4.0000/5 = 80.00% |
| Qwen3-4B | Required Evidence Coverage | 8 | 6/8 = 75.00% |
| Evidence selection | Precision | — | 11/120 = 9.17% |
| Evidence selection | Recall | — | 11/13 = 84.62% |
| Evidence selection | Selection loss | — | 0/7 = 0% |

BM25/FAISS/RRF/Qwen3 rankings and fragment identifiers are preserved per question in `timings.csv`. The selected set is now measured from the `evidence_selection` trace, not incorrectly from the reranker trace.

## Component metrics without independent labels

The following remain `N/A` with explicit reasons in `architecture_metrics.csv`: DeepSeek intent/entity/temporal accuracy; generation claim support and required-fact recall; verifier error precision/recall/F1 and false-pass/rejection; citation attribution/version accuracy; semantic follow-up trigger precision/recall. Runtime events alone cannot independently establish these properties, and model self-reports are not used.

Deterministic invariants available from the run: BGE-M3 dimension `1024`; Qwen3 candidates `24`, output cutoff `8`; API errors `0`; retries `0`; all five question traces present. Follow-up trigger trace: `2/4` questions with a recorded question-stage event; length/language semantic quality is `N/A` because the event stream stores only the boolean addition flag, not the final answer text.

## Per-question status and losses

| ID | Pipeline state | Trace | Objective issue |
|---|---|---|---|
| H3 | success | complete | no independent generation label |
| P2 | fallback after verifier error | complete | verifier returned no valid verdict |
| R5 | success | complete | follow-up was added; semantic trigger correctness N/A |
| C3 | success | complete | no independent generation label |
| Fx2 | success | complete | follow-up was added; semantic trigger correctness N/A |

Exact lost evidence is available as set differences between each event’s `ranked_fragment_ids` and `fragment_ids`; no loss is silently converted to `N/A`. The aggregate selection loss is `0/7`, while recall loss occurs earlier in BM25/RRF/Qwen3 as shown above.

## Before/after and reproducibility

| Metric | Historical P03 | Current replay |
|---|---:|---:|
| FAISS Section Recall@24 | 93.33% | 93.33% |
| RRF Section Recall@24 | 73.33% (historical) | 86.67% |
| Qwen3 Section Recall@8 | 66.67% (historical) | 80.00% |
| Required Evidence Coverage | 6/8 (historical) | 8/8 FAISS; 6/8 Qwen3; 11/13 final recall |

These are not a claim of model-quality improvement across versions: historical and current runs have different runtime conditions. The comparison is displayed only as same-ID context.

Re-running `python -m submission.evaluate_golden --recalculate --run-id replay-d97d552-p03` uses saved events and reproduces the current architecture metrics without model calls. Missing traces raise explicit errors during relevance loading rather than becoming successful `N/A` rows.

## Validation

- pytest: `64 passed`
- Ruff: passed
- compileall: passed
- No Golden files, evaluation data or diagnostics added to the competition ZIP by this change.
- No Codex score, DeepSeek judge, or fourth LLM used.
