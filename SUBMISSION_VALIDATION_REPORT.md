# Submission validation report

## Archive

- Path: `PSB_submission.zip`
- Size: 4,275,916 bytes (below 128 MiB)
- Files: `solution.py`, `requirements.txt`, required flat runtime modules,
  embedded index, and embedded prompts only.
- Golden data, tests, reports, logs, `.env`, secrets, development helpers, and
  raw corpus files are absent.

## Clean-directory execution

The archive was extracted to a fresh temporary directory and executed against
the organizer demo questions and knowledge-base ZIP with the repository virtual
environment's Python executable.

- Process exit code: 0
- Input IDs: 18
- Output rows: 18
- Unique output IDs: 18
- Empty answers: 0
- Maximum answer length: 64 characters
- CSV columns: `id,answer`
- Embedded index: loaded and checksum-validated, 21 documents / 1,034 chunks

No model credentials were exported. Consequently all 18 demo answers were the
explicit technical-failure fallback, and API-dependent quality is not validated
by this run. This run proves archive structure, imports, index compatibility,
input/output handling, CSV encoding, and graceful failure only.

