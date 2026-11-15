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

