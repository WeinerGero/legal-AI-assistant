# RAG component quality report — P03

HEAD: `9988ea8` (code under test)  
Run: `replay-9988ea8-p03`  
Cases: H3, P2, R5, C3, Fx2. Historical P03 was not overwritten.

The new real P03 replay completed 5/5 cases using DeepSeek №1–3, BGE-M3/FAISS, BM25 and local Qwen3-Reranker-4B. No judge model or fourth LLM was used. Recalculation was then run from saved events only and produced the same CSV rows.

## Retrieval metrics (P03 independent annotation v1)

Values are macro over 5 annotated questions unless marked micro. Numerators/denominators are retained in `architecture_metrics.csv`.

| Component | Metric | K | Result |
|---|---|---:|---:|
| BM25 | Section Recall | 24 | 2.5/5 = 50.00% |
| BM25 | Fragment Recall | 24 | N/A in summary table; raw CSV retained |
| FAISS | Section Recall | 24 | 5/5 = 100% |
| FAISS | Fragment Recall | 24 | N/A in summary table; raw CSV retained |
| FAISS | Required Evidence Coverage | 24 | 8/8 = 100% |
| RRF (FAISS default) | Section Recall | 24 | 5/5 = 100% |
| RRF (FAISS default) | Fragment Recall | 24 | 5/5 = 100% |
| RRF (FAISS default) | Required Evidence Coverage | 24 | 8/8 = 100% |
| Qwen3-4B | Section Recall | 8 | 4.0000/5 = 80.00% |
| Qwen3-4B | Fragment Recall | 8 | 4.0000/5 = 80.00% |
| Qwen3-4B | Required Evidence Coverage | 8 | 6/8 = 75.00% |
| Evidence selection | Precision | — | 13/120 = 10.83% |
| Evidence selection | Recall | — | 13/13 = 100% |
| Evidence selection | Selection loss | — | 0/8 = 0% |

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

## Why RRF lost evidence and what changed

The old 0.5/0.5 fusion let BM25-only items outrank FAISS hits at a fixed top-24 cutoff. The replay-only benchmark showed FAISS-only was best on P03; the weighted and diverse variants were lower. The default is therefore now `RRF_MODE=faiss`, while weighted/diverse modes remain diagnostic. In the new real run RRF is equivalent to FAISS and retains all 8 required evidences.

The previous “8 → 6” loss was between the old fused/reranked top-8 and Qwen3 output, not between FAISS@24 and evidence selection. The new stage traces expose the exact ordered IDs at BM25 → FAISS → RRF → Qwen input/output → selection → generation; the aggregate new run has no selection loss (`0/8`).

Evidence Precision used denominator 120 because it counts all selected fragments across five questions (24 per question); Selection Loss used denominator 7/8 because it counts only required fragments present before selection. They are different quantities.

The follow-up denominator was 4 because one question had no question-stage quality event after a fallback. This is now reported as a trace-completeness limitation, not as a five-question semantic denominator.

## Before/after and reproducibility

| Metric | Historical P03 | Current replay |
|---|---:|---:|
| FAISS Section Recall@24 | 93.33% | 100% (5/5) |
| RRF Section Recall@24 | 73.33% (historical) | 100% (5/5) |
| Qwen3 Section Recall@8 | 66.67% (historical) | 80.00% (4/5) |
| Required Evidence Coverage | 6/8 (historical) | 8/8 RRF; 6/8 Qwen3; 13/13 final recall |

These are not a claim of model-quality improvement across versions: historical and current runs have different runtime conditions. The comparison is displayed only as same-ID context.

Re-running `python -m submission.evaluate_golden --recalculate --run-id replay-9988ea8-p03` uses saved events and reproduces the current architecture metrics without model calls. Missing traces raise explicit errors during relevance loading rather than becoming successful `N/A` rows.

## Validation

- pytest: `64 passed`
- Ruff: passed
- compileall: passed
- No Golden files, evaluation data or diagnostics added to the competition ZIP by this change.
- No Codex score, DeepSeek judge, or fourth LLM used.
