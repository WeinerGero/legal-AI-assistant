# Backend RAG integration

This layer attaches the verified competition RAG to the unchanged FastAPI object from
`backend.app`. It loads one prebuilt BGE-M3/FAISS index, retains BM25/RRF/Qwen3 search,
uses DeepSeek analysis/generation/verification, and converts verified claims and exact
corpus spans to the strict backend `CoreResult` contract.

## Prerequisites

- Docker Desktop with `host.docker.internal` support;
- BGE-M3 at `http://127.0.0.1:8081/v1/embeddings`;
- Qwen3 reranker at `http://127.0.0.1:8080/v1/rerank`;
- the existing database/login variables and `LLM_DEV` in the root `.env`.

Do not put secrets in this repository. The separate integration image packages the tracked
`data/organizers/knowledge-base` directory into `/app/data/knowledge-base.zip` during its
build. The resulting archive is verified against the bundled index manifest at startup.
DeepSeek uses `https://matreshkaapi.mooo.com/v1`, `LLM_DEV`, and the explicit model
`deepseek-v4.1-flash`.

`CORPUS_ID` is derived from the actual archived document hashes and
`INDEX_GENERATION_ID` from the bundled manifest SHA-256 at container startup. Neither
identifier needs to be configured manually.

## Start

From the repository root in PowerShell:

```powershell
$env:FRONTEND_PORT = '8082'
docker compose -f docker-compose.yml -f integration/docker-compose.rag.yml config
docker compose -f docker-compose.yml -f integration/docker-compose.rag.yml up --build
```

Open `http://localhost:8082`. API remains at `http://localhost:8001`; RAG readiness is
available at `http://localhost:8001/integration/readyz` after application startup.

The integration limits Qwen3/model concurrency to one request and bounds the waiting
queue. The interactive Qwen3-4B batch is bounded to 12 candidates and six returned
evidence fragments. Model calls time out after 300 seconds; frontend and nginx use 360
seconds.

## Integrity check

Protected paths must have no diff against `origin/back+front`:

```powershell
git diff --exit-code origin/back+front -- .dockerignore .env.example Dockerfile README.md backend docker-compose.yml frontend.Dockerfile frontend
```

The Compose overlay is additive; it does not create another PostgreSQL service or a
second FastAPI route set.
