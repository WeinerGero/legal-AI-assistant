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

The integration does not bundle the private corpus and never reads or copies `.env` into
the image. Real end-to-end model checks require the corpus ZIP, model services and secret
environment variables at runtime. A technical model/service failure remains a technical
failure and is not converted to `insufficient_evidence`.

Final test, timing, Compose reachability and integrity results are recorded in the commit
handoff rather than fabricated when the external services are unavailable.
