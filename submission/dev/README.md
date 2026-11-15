# DEV_LOCAL model integration

This directory is only for local validation and is not included by
`submission/build_submission.py` in the competition ZIP.

The PowerShell entry point configures all local endpoints in its child process.
It does not persist environment variables in the parent console and never reads
or prints `.env`. Export `LLM_DEV` in the parent process before launching the
script. The competition runtime continues to use organizer-provided environment
variables and does not require `.env`.

## Commands

Run from the repository root with Windows PowerShell 5.1 or newer:

```powershell
submission\dev\run_local.ps1 preflight
submission\dev\run_local.ps1 smoke
submission\dev\run_local.ps1 evaluate -MaxWorkers 2
```

- `preflight` makes real DeepSeek, BGE-M3 and Qwen3 requests, checks BGE-M3
  dimension and normalization against the manifest, and performs a real FAISS
  search.
- `smoke` runs the existing pipeline on demo questions 1 and 5.
- `evaluate` runs the existing pipeline on all 18 demo questions and creates
  an independent quality report.

Local outputs and API recordings are written below
`submission/evaluation_runs/`, which is ignored by Git and omitted from the
competition archive.

DEV_LOCAL is strict: unavailable embeddings, an incompatible FAISS index or a
reranker failure stops the command. The competition mode keeps its existing
fallback behavior.
