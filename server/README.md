# Server

FastAPI API and the ingest worker (`python -m app.ingest.worker`). Same
code, same Docker image. Python 3.12, managed with uv. See
[DEVELOPMENT.md](../DEVELOPMENT.md) for the stack, host dev servers, checks and
migrations.

- Configuration: `app/settings.py` (pydantic-settings) reads the environment
  and `server/.env`. `.env.example` lists every setting.
- In compose, the `server` command runs `app/scripts/run_migrations.py` (Alembic
  `upgrade head`), then `gunicorn -c gunicorn.config.py app.main:app`.
- Host dev: `uv run uvicorn app.main:app --reload --reload-exclude '.repo_snapshots/*' --port 8003`.
- Checks: `sh scripts/check.sh` (pytest, ruff, ruff format, pyright).
- OpenAPI: `uv run python -m app.scripts.export_openapi <file>` (the client's
  `yarn gen:api` calls this). `tests/test_openapi_snapshot.py` keeps the
  client's copy current.
- Swagger UI: `/docs` (compose: `http://localhost:12001/docs`).

## Layout

| Path | What |
|---|---|
| `app/main.py` | app, CORS, routers under `/api/...` |
| `app/api/` | HTTP routes (`paper/` package, uploads, highlights, notes documents, chat messages, projects, discover, settings, ...) |
| `app/database/` | SQLAlchemy 2.0 models (`models/`, one module per domain), CRUD (`crud/`), read queries (`queries/`) |
| `app/ingest/` | ingest v2: stage graph, stages, worker, service; contracts in `app/ingest/README.md` |
| `app/llm/` | model registry and slots, one-shot calls, paper chat and quick question (`chat/`), agent tools (`tools/`), companion-repo sandbox (`repo/`) |
| `app/references/` | bibliography entry resolution for the reader's citation hover cards |
| `app/auth/` | email-code sign-in, sessions |
| `app/core/` | shared error classification, retry/backoff, deadlines, HTTP client |
| `migrations/` | Alembic; squashed baseline `baseline_20260924` + later revisions |
| `tests/` | pytest; Postgres-backed tests use a throwaway `postgres:17` container |
