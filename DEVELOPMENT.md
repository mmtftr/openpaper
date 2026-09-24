# Development Setup

This project consists of three main components: a `server`, a `client`, and a `jobs` service.

## Docker Compose Quick Start

Use Docker Compose for local development instead of tmux or separately managed service processes.

1. Copy and fill the service env files:
   ```bash
   cp server/.env.example server/.env
   cp jobs/.env.example jobs/.env
   ```

2. Set `OPENAI_API_KEY` in both env files. For Azure OpenAI, additionally set `AZURE_OPENAI=true` and `AZURE_OPENAI_ENDPOINT` in both files; `OPENAI_API_KEY` is treated as the Azure key in that mode and structured outputs are auto-patched for Azure's strict JSON-schema rules.

3. Start the stack:
   ```bash
   docker compose up --build
   ```

4. Open:
   - Client: `http://localhost:9002`
   - API docs: `http://localhost:9003/docs`
   - Jobs API health: `http://localhost:9004/health`
   - RabbitMQ console: `http://localhost:15672` (`guest` / `guest`)
   - MinIO console: `http://localhost:9001` (`openpaper` / `openpaper-local`)

The compose stack includes Postgres, RabbitMQ, Redis, and MinIO. It runs database migrations before the server starts and uses MinIO as local S3-compatible object storage.

The compose HTTP ports stay in the `900x` range: MinIO on `9000`, MinIO console on `9001`, client on `9002`, server API on `9003`, and jobs API on `9004`.

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

## 4. Set Up the Asynchronous Jobs Service

The jobs service handles long-running tasks like PDF processing.

Detailed instructions can be found in the [jobs/README.md](./jobs/README.md).

**Quick Start:**
1.  Navigate to the `jobs` directory: `cd jobs`
2.  Create and activate virtual environment:
    ```bash
    uv venv
    source .venv/bin/activate
    ```
3.  Install dependencies: `uv install`
3.  Start RabbitMQ and Redis (e.g., using Docker).
4.  Start the Celery worker: `./scripts/start_worker.sh`
