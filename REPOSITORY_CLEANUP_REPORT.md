# Repository cleanup report

## Scope and result

The review covered tracked `core/`, `submission/`, `tests/`, documentation,
the ZIP builder, prompt experiments, evaluation scripts, the embedded BGE-M3
index, and ignored run artifacts. No tracked file was deleted.

## Decisions

- `core/` and `submission/` are intentionally different layers: `core/` is
  the port/domain design and `submission/` is the flat self-contained contest
  runtime. Neither is an unused duplicate.
- `submission/evaluate_quality.py`, `evaluate_live_run.py`,
  `evaluate_prompt_experiments.py`, metrics, and recording remain referenced by
  local evaluation or DEV_LOCAL preflight and were retained.
- Candidate prompts under `core/prompts/experiments/` remain disconnected from
  runtime defaults and are retained as reproducible experiment inputs.
- Historical reports are small tracked evidence for earlier 18-case work. They
  were not treated as current 65-case results and were retained.
- `submission/evaluation_runs/`, API recordings, bytecode, and build outputs are
  ignored local artifacts. They were not added to Git or to the contest ZIP.
- The exact 65-case dataset was added under `tests/golden/`; it is excluded from
  the contest ZIP by the builder allowlist.

## Structural changes

- Agent boundaries now permit the explicitly authorized combined participant
  1 and participant 3 work while preserving participant 2 ownership.
- Fallback document splitting now records exact source character ranges.
- Golden evaluation is centralized in `submission/evaluate_golden.py`.
- The obsolete DeepSeek Golden judge and its dedicated schema test were removed.
  It was referenced only by that test, was excluded from the contest builder,
  and conflicts with the current independent Codex review policy. Historical
  judge outputs in ignored experiment artifacts were preserved.
- The universal RAG policy is now consistent across `.agents`, `AGENTS.md`,
  automated-assistant instructions, and the architecture document.
- Golden execution now persists one completed case atomically before starting
  the next case; interrupted partitions resume without overwriting completed IDs.
- P03 provision-level relevance labels and canonical mixed-version run selection
  are stored under `tests/golden/`; neither is imported by the runtime or packed
  into the competition ZIP.
- Search metrics reuse the existing evaluator instead of adding a parallel
  scoring tool. The diagnostic 24-candidate Qwen3 order is DEV_LOCAL-only and
  the production evidence list remains top-8.
- No fourth LLM evaluator is present. Golden scoring is an offline Codex review;
  DeepSeek remains limited to analysis, generation, verification, and revision.

