# Integration report

## Provenance and isolation

- protected source: `origin/back+front` at `f4458073b0dfb1c3fb6de50be4819cc9d10adfa1`;
- verified RAG source: `3928eb04c89456d32294b01e83111102dafb31bb`;
- target branch: `feature/backend-rag-integration`, created in a separate worktree;
- protected backend/frontend/Docker files are intentionally unchanged.

Runtime files were restored as Git blobs from the verified RAG commit. Golden datasets,
historical reports and evaluation runners were not transferred.

## Connected path

`integration.app:create_app` imports the existing `backend.app.app`, wraps its existing
lifespan, initializes one `RagHandler`, assigns `app.state.core_handler`, and releases it
on shutdown. Existing authentication, database, conversations and source routes remain
owned by the backend.

The handler preserves conversation history, active-document context, event date,
summary and context revision. The same verified structured generation used for the CSV
answer is converted directly into backend blocks and citations; no second generation or
regular-expression reconstruction is used.

Source locations are published only after the exact invariant
`canonical_text[start_char:end_char] == fragment.text` passes. Document, version and
clause UUIDs are deterministic internal UUIDv5 values derived from stable source/version
identity; they are not represented as organizer identifiers.

## Operational limits

The integration packages the 21 tracked organizer documents into its separate image but
never reads or copies `.env` into the image. Real end-to-end model checks require model
services and secret environment variables at runtime. A technical model/service failure
remains a technical failure and is not converted to `insufficient_evidence`.

Final test, timing, Compose reachability and integrity results are recorded in the commit
handoff rather than fabricated when the external services are unavailable.

## Verification performed on 2026-11-15

- integration image built successfully with Docker Desktop 29.7.2;
- the combined Compose configuration validated successfully with frontend port 8082;
- BGE-M3 (`8081`) and Qwen3 (`8080`) `/v1/models` returned HTTP 200 from both the
  Windows host and an integration container through `host.docker.internal`;
- three container tests passed, including strict schema conversion, conversation context,
  authentication, persistence, message history, source persistence, source lookup, exact
  highlight text and an unknown-source 404;
- Ruff passed for `core`, `submission`, `integration` and `tests`;
- compilation passed; the transferred verified RAG snapshot retains pre-existing mypy
  findings, so mypy is not reported as passing;
- all 19 protected files match `origin/back+front` by Git tree, blob SHA and SHA-256.

No real DeepSeek legal answer was generated in the earlier run because model credentials
were not read by the agent. Reporting model-answer quality or per-stage live timing without
an authenticated call would be misleading. The tracked organizer corpus is now packaged
directly by the integration image and does not require a host mount.

The runtime now receives `LLM_DEV` from Compose, fixes `LLM_MODEL` to
`deepseek-v4.1-flash`, and derives `CORPUS_ID` and `INDEX_GENERATION_ID` from the actual
corpus hashes and bundled manifest rather than operator-supplied labels.

## Runtime configuration verification

The corrected stack was rebuilt and started on 2026-11-15. `docker compose config
--quiet` passed using the root `.env` without printing it. `/integration/readyz` returned
`rag_ready=true`, the explicit `deepseek-v4.1-flash` model, a corpus identifier derived
from the 21 archived document hashes, and an index-generation identifier derived from the
manifest SHA-256.

A real authenticated backend request asked for the payment term under supply contract
P-17-2024. It completed in 71.673 seconds with HTTP 201, `legal_status=partial`,
`verification_status=partial`, and three persisted sources. All three source routes opened
with non-empty exact highlights. The verified partial answer did not retain a supported
claim that directly stated the requested payment term; this is a quality/retrieval gap,
not a configuration failure.

The first live attempt failed because the local Qwen3-4B server disconnected on a batch
of 30 long fragments. A short rerank request from the same container returned HTTP 200.
Interactive configuration was therefore bounded to 12 rerank candidates and six final
evidence fragments; the real request then completed without disabling Qwen3 or using an
Ollama/additional-model fallback.

## Merge simulation against current dev

`origin/dev` was `b0fe048eb436d9ec5a8e9a6544ca73aaa0c0591c`. `git merge-tree --write-tree`
reported conflicts in `.env.example`, `submission/build_submission.py`,
`submission/model_client.py`, `submission/pipeline.py`, `submission/search.py` and
`submission/solution.py`. There were no backend or frontend conflicts. The conflicts arise
because `dev` contains an independently evolved RAG and does not yet contain the protected
`back+front` head; they must be resolved explicitly in a later integration PR.
