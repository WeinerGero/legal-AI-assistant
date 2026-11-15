# Backend MVP

The API stores one automatically created user, conversations, messages, and
structured answers. It uses PostgreSQL in Docker Compose and SQLite when run
locally without `DATABASE_URL` or `DB_HOST`.

Before `docker compose up --build`, set these variables in the repository-root
`.env` file:

```dotenv
POSTGRES_PASSWORD=<set-me>
APP_USER_LOGIN=<set-me>
APP_USER_PASSWORD=<set-me>
```

The API is available at `http://localhost:${API_PORT:-8001}`; OpenAPI docs are
at `/docs`. Set `API_PORT` in `.env` to override the host port. After login,
send the returned bearer token in the `Authorization` header.
Main routes are `/api/v1/auth/*`, `/api/v1/conversations`, and
`/api/v1/conversations/{id}/messages`.

Send a message with the selected document context:

```json
{
  "content": "Question about the contract",
  "context": {
    "active_document": {
      "contract_number": "<contract-number>",
      "counterparty": "<counterparty>"
    },
    "event_date": null
  }
}
```

When wiring the core handler, also set `CORPUS_ID` and
`INDEX_GENERATION_ID` in `.env`.

Message history is stored before the core is called. The core handler receives
the question JSON described in `docs/ARCHITECTURE.md`, with up to the six
preceding messages. It uses `CORPUS_ID` and `INDEX_GENERATION_ID` from `.env`.
Each message request includes a `context` object with the active document and
event date. The handler must return the answer JSON with citations and sources.
The handler is not wired while `LegalPipeline.run()` remains a stub, so message
requests return a structured 503 rather than inventing an answer.
