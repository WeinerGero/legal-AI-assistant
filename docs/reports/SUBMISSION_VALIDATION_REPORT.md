# Submission validation report

Latest rebuild after component-quality changes: `artifacts/PSB_submission.zip`, 4,282,127 bytes.
The archive contains 11 root files, compiles after extraction into an isolated
temporary directory, and contains no Golden, judge, evaluation, backend-example,
secret, log, or development files. The builder still emits ordinary
`submission.csv` text through the `id,answer` contract; P02 produced five
nonempty Russian text answers rather than internal JSON.

The independent backend contract archive is
`artifacts/PSB_BACKEND_JSON_EXAMPLES.zip` (3,541 bytes). Its schema and eight
examples validate against `core.contracts.RenderedAnswerV1` and it is not nested
inside the competition archive.

## Archive

- Path: `PSB_submission.zip`
- Size: 4,282,127 bytes (below 128 MiB)
- SHA-256: `F8E8E01A2BEFD7CBA9292F06AAE67CD3C4B019EB370ED3213B1A756482ECD12B`.
- Files: `solution.py`, `requirements.txt`, required flat runtime modules,
  embedded index, and embedded prompts only.
- Golden data, tests, reports, logs, `.env`, secrets, development helpers, and
  raw corpus files are absent.
- Golden evaluation and Codex assessment tooling are excluded by the builder allowlist; the obsolete DeepSeek Golden judge was removed from the repository.
- All ten Python runtime files in the rebuilt archive compile from their archived bytes; the flat archive contains 11 allowed root files and no forbidden evaluation material.
- The five real P03 outputs used the `id,answer` CSV contract and ordinary Russian text; no raw `Fxxx`, `E###`, workflow state, or serialized internal JSON was published.

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

