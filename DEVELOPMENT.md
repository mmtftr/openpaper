# Development

One host runs everything with Docker Compose; a host Caddy puts it on the
tailnet at `https://$BASE_HOSTNAME`. For day-to-day iteration you can run the
API and the client on the host, against the same compose Postgres and MinIO.

## The stack

`docker compose up -d --build` merges `compose.yaml` with
`compose.override.yaml` automatically.

| Service | What | Host port (127.0.0.1) |
|---|---|---|
| `postgres` | Postgres 17, db `openpaper` | not published (5434 with `compose.dev.yaml`) |
| `minio` + `minio-init` | local S3, bucket `openpaper-local` | 12010 (S3), 12011 (console, `openpaper` / `openpaper-local`) |
| `server` | FastAPI under gunicorn (`WEB_CONCURRENCY`, default 3 workers); runs migrations first | 12001 (`/docs` for Swagger) |
| `ingest-worker` | `python -m app.ingest.worker`, same image and env as `server`; starts once the server is healthy | none |
| `client` | Next.js standalone server (`node server.js`) | 12000 |

Configuration:

- `.env` (repo root, from `.env.example`): compose interpolation.
  `BASE_HOSTNAME` sets the URLs in `compose.yaml`; `NEXT_PUBLIC_API_URL` and
  `MAX_UPLOAD_SIZE_MB` are compiled into the client image; `ADMIN_EMAILS`.
- `server/.env` (from `server/.env.example`): API keys, providers, models,
  Mistral OCR. It is read by `app/settings.py` (pydantic-settings). Compose's
  `environment:` entries override it for the database, S3, domain and cookie
  values.
- `compose.override.yaml` switches those to `https://$BASE_HOSTNAME`
  (secure cookies, S3 served at `/s3/openpaper-local`).

Environment changes need a recreate. `docker compose restart` keeps the old
env. `NEXT_PUBLIC_*` changes need a client rebuild.

```bash
docker compose up -d --force-recreate server ingest-worker   # after editing server/.env
scripts/rebuild.sh [service ...]   # build, recreate, prune dangling images
```

### The Caddy deploy

The host Caddy (`~/p/tmp/caddy/Caddyfile`, tailnet-only) serves one origin:
`/api/*` → `127.0.0.1:12001` (prefix kept), `/s3/*` → `127.0.0.1:12010`
(prefix stripped), everything else → `127.0.0.1:12000`. Because the API is
same-origin, the client middleware can see the session cookie and redirect
to `/login`.

### Signing in

Sign-in is by email code. Without `RESEND_API_KEY`, the server logs the code
(`docker compose logs server | grep "DEV LOGIN CODE"`). Emails in
`ADMIN_EMAILS` get `is_admin` on signup. For scripts, insert a `sessions`
row and send its token as the `session_token` cookie; `scripts/smoke.py`
does this.

## Ingest

An upload (`POST /api/paper/upload`, or `/from-url`) runs the `source` stage
in the request: it validates and stores the PDF under `papers/{id}/` in S3 and
queues the other stages, so the reader works immediately. The worker then
runs the stage DAG: preview thumbnail, text layer, OCR (Mistral), OCR repair,
metadata (DOI, Crossref, OpenAlex, arXiv, with an LLM fallback), figures,
outline and AI highlights. It retries each stage with backoff.

- Queue and progress: the `ingest_stages` rows. Heartbeat: the single
  `ingest_worker` row; the paper header's status popover says "Ingest
  worker offline" when it's stale. Retry and reprocess are in that popover.
- Logs: `docker logs -f openpaper-ingest-worker-1`.
- Design: `docs/INGEST_DESIGN.md`. Code contracts (stages, context, service
  operations, read side): `server/app/ingest/README.md`.
- Run exactly **one** worker per database. If you run one on the host,
  `docker compose stop ingest-worker` first.

## Models

Providers are configured in `server/.env`: OpenAI (or Azure OpenAI with
`AZURE_OPENAI=true`), an OpenAI-compatible `CODEX_PROXY_*` endpoint,
Anthropic and Gemini. Each provider's `*_MODELS` list feeds the chat model
picker. `DEFAULT_LLM_PROVIDER` picks the default provider.

Every call site uses a named slot (`app/llm/model_slots.py`: `chat.default`,
`chat.title`, `chat.reconcile`, `quick_question`, `discover`, `ingest.*`). A
slot's default is a (provider, default/fast role) pair that follows the env.
**Settings → Models** (`/settings/models`; `GET /api/settings/models`,
`PUT /api/settings/models/{slot}`)
stores per-slot overrides in the `model_slots` table. Other gunicorn workers
pick an override up within a short TTL. An override that stops resolving is
logged and ignored.

## Host dev servers

Publish Postgres on the host, then run uvicorn and `next dev` against the
compose infra. They share its data.

```bash
docker compose -f compose.yaml -f compose.override.yaml -f compose.dev.yaml up -d postgres

# API on :8003. Real env vars win over server/.env.
cd server
DATABASE_URL=postgresql://postgres:postgres@localhost:5434/openpaper \
S3_ENDPOINT_URL=http://127.0.0.1:12010 CLIENT_DOMAIN=http://localhost:8002 \
  uv run uvicorn app.main:app --reload --reload-exclude '.repo_snapshots/*' --port 8003

# Client on :8002 (client/.env.local: NEXT_PUBLIC_API_URL=http://localhost:8003)
cd ../client && yarn && yarn dev -p 8002
```

The host API also needs the S3 settings that compose normally injects
(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` = `openpaper` /
`openpaper-local`, `S3_BUCKET_NAME`, `S3_PUBLIC_BASE_URL`).
`server/.env.example` has host-dev values for all of them. The `--reload-exclude` matters: repo ingestion writes snapshots under
`server/.repo_snapshots/`, and a reload would kill it mid-way. Run `yarn dev`,
not `npx next dev`: its `predev` hook copies the pdf.js assets into
`public/pdfjs/<version>/` (see `client/README.md`).

## Typed API

The server's FastAPI request/response models are the source of truth. The
client's `src/lib/api/openapi.json` and `schema.d.ts` are generated from them,
and `src/lib/api/client.ts` wraps them with `openapi-fetch`.

```bash
cd client
yarn gen:api     # export_openapi.py → openapi.json → openapi-typescript → schema.d.ts
yarn check:api   # schema.d.ts is current with openapi.json
```

`server/tests/test_openapi_snapshot.py` fails when `openapi.json` no longer
matches the app, so an API change needs `yarn gen:api`, and the regenerated
files are committed with it.

## Checks

```bash
sh server/scripts/check.sh   # pytest + ruff check + ruff format --check + pyright

cd client
npx tsc --noEmit -p .
yarn lint                    # next lint; warnings OK, no errors
yarn check:api
yarn build

uv run scripts/smoke.py [--paper-id ID] [--skip-chat]   # against the running stack
```

The Postgres-backed tests start a throwaway `postgres:17` container, so they
need Docker, or `INGEST_TEST_DATABASE_URL` pointing at a disposable database.
Some are skipped unless that variable is set. `smoke.py` opens a paper
(detail, PDF, figures, outline), runs one chat turn, creates and deletes a
highlight with a note, and loads the paper page. It deletes whatever it
creates.

## Migrations

Alembic, in `server/migrations/`. The history was squashed into one baseline
(`baseline_20260924`, DDL in `versions/baseline.sql`), and later revisions
build on it. Every `server` start runs `app/scripts/run_migrations.py`
(create the database if missing, then `alembic upgrade head`) before
gunicorn.

```bash
docker exec openpaper-server-1 alembic current
cd server && DATABASE_URL=postgresql://postgres:postgres@localhost:5434/openpaper \
  uv run alembic revision --autogenerate -m "what changed"   # needs compose.dev.yaml
```

## Dependencies

- Server: the image installs exactly what `server/uv.lock` pins. `[tool.uv]
  exclude-newer` in `server/pyproject.toml` is a release-age cooldown. To bump
  a dependency, move that date forward (never to less than about a week ago)
  and run `uv lock`.
- Client: yarn 1 with `yarn.lock` (`yarn install --frozen-lockfile` in the image).
