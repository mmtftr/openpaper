# Development Setup

This project consists of two components: a `server` (FastAPI, plus the ingest worker that processes uploaded papers — same code and image) and a `client` (Next.js).

## Docker Compose Quick Start

Use Docker Compose for local development instead of tmux or separately managed service processes.

1. Copy and fill the env file:
   ```bash
   cp server/.env.example server/.env
   ```

2. Set `OPENAI_API_KEY` and `MISTRAL_API_KEY` (OCR) in `server/.env`. For Azure OpenAI, additionally set `AZURE_OPENAI=true` and `AZURE_OPENAI_ENDPOINT`; `OPENAI_API_KEY` is treated as the Azure key in that mode and structured outputs are auto-patched for Azure's strict JSON-schema rules.

3. Start the stack:
   ```bash
   docker compose up --build
   ```

4. Open:
   - Client: `http://localhost:12000`
   - API docs: `http://localhost:12001/docs`
   - MinIO console: `http://localhost:12011` (`openpaper` / `openpaper-local`)

The compose stack includes Postgres and MinIO (local S3-compatible object storage). The server runs database migrations before it starts; the `ingest-worker` service (`python -m app.ingest.worker`, same image as the server) starts once the server is healthy and processes uploaded papers stage by stage (`docs/INGEST_DESIGN.md`). Its progress is in the `ingest_stages` table and in the paper header's status popover.

Host ports (bound to `127.0.0.1`): client `12000`, server API `12001`, MinIO `12010`, MinIO console `12011`.

## Reaching the stack from another machine

By default everything binds to `localhost`. To make the stack reachable from your LAN, Tailscale, or behind a reverse proxy, set `BASE_HOSTNAME` to the host's public name before bringing the stack up:

```bash
BASE_HOSTNAME=mybox.tail-scale.ts.net docker compose up --build
```

`BASE_HOSTNAME` is interpolated into `CLIENT_DOMAIN`, `API_DOMAIN`, `S3_PUBLIC_BASE_URL`, the client's `NEXT_PUBLIC_API_URL` build arg, and the `extra_hosts` entries that let containers resolve the host's public hostname back to the docker host gateway.

If you front the stack with nginx on a single hostname (so the browser hits `/api/...` on the same origin as the client), leave `NEXT_PUBLIC_API_URL` unset — `lib/api.ts` falls back to relative URLs in that case.

## Local admin account

Set `ADMIN_EMAILS` (in `server/.env` or as a compose-level env var) to a comma-separated list of emails. On signup, those users automatically receive `is_admin=true`. Compose defaults to `admin@local.openpaper` — override with `ADMIN_EMAILS=you@example.com docker compose up`.

Sign in normally via the email-link flow; the verification code is logged by the server (no real email sending needed for local dev).

## Manual Setup

Each component can still be run manually when needed.

## 1. Clone the Repository

First, clone the project repository:

```bash
git clone git@github.com:sabaimran/openpaper.git
cd openpaper
```

## 2. Set Up the Backend Server

The backend server is a Python application that manages data and communicates with the other services.

Detailed instructions can be found in the [server/README.md](./server/README.md).

**Quick Start:**
1.  Navigate to the `server` directory: `cd server`
2.  Create and activate virtual environment:
    ```bash
    uv venv
    source .venv/bin/activate
    ```
3.  Install dependencies: `uv pip install -r pyproject.toml`
3.  Set up your `.env` file with database and API keys.
4.  Run database migrations: `python3 app/scripts/run_migrations.py`
5.  Start the server: `python3 -m app.main`

## 3. Set Up the Frontend Client

The frontend is a Next.js web application.

Detailed instructions can be found in the [client/README.md](./client/README.md).

**Quick Start:**
1.  Navigate to the `client` directory: `cd client`
2.  Install dependencies: `yarn`
3.  Run the development server: `yarn dev`

## 4. Run the Ingest Worker

Uploaded papers are processed (OCR, metadata, figures, outline, AI highlights) by the ingest worker, a second process from the server's code base. With the server's `.env` in place:

```bash
cd server
uv run python -m app.ingest.worker
```

One worker per deployment. It polls `ingest_stages` for queued work and writes a heartbeat to `ingest_worker` (the paper UI says "worker offline" when it's stale).
