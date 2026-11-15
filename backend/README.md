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

The API is available at `http://localhost:8000`; OpenAPI docs are at `/docs`.
After login, send the returned bearer token in the `Authorization` header.
Main routes are `/api/v1/auth/*`, `/api/v1/conversations`, and
`/api/v1/conversations/{id}/messages`.

Message history is stored before the core is called. The core handler receives
an `AnalysisRequestV1` JSON payload with up to the six preceding messages and
must return a `RenderedAnswerV1` JSON value. The handler is not wired while
`LegalPipeline.run()` remains a stub, so message requests return a structured
503 rather than inventing an answer.
