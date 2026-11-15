# Submission validation report

## Archive

- Path: `PSB_submission.zip`
- Size: 4,275,916 bytes (below 128 MiB)
- Files: `solution.py`, `requirements.txt`, required flat runtime modules,
  embedded index, and embedded prompts only.
- Golden data, tests, reports, logs, `.env`, secrets, development helpers, and
  raw corpus files are absent.
- Independent `golden_judge.py` is excluded by the builder allowlist.

## Official-material comparison

- Technical regulation SHA-256: `11BF7862477D1B9A23C69F852A7FF74690E35E7DE4CAE80BFE043B5B8B491406`.
- Organizer sample ZIP SHA-256: `99227787858DD5106B1902D5C44AE2399F37EFB63BEAE20683FE467F898D640F`.
- Runtime accepts the documented `LLM_*` variables and `BASE_URL` / `OPENAI_*`
  alternatives, uses the same proxy and fallback key for generation,
  embeddings, and reranking, and does not invoke `pip install`.
- The archive is flat like the organizer sample. Omitting README is permitted
  by the sample instructions.

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

