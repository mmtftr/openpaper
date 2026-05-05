# CLAUDE.md

## Deployment shape

Everything runs via Docker Compose on this host. `compose.yaml` is canonical;
`compose.override.yaml` overlays the `paper.example.com` deploy (HTTPS, secure
cookies, S3 served behind Caddy at `/s3/...`). Compose merges them
automatically — `docker compose up` brings up the production-ish setup.

Containers and roles:
- `client` (Next.js) → `server` (FastAPI/gunicorn) → `postgres`
- `jobs-api` + `jobs-worker` (Celery) talk to `rabbitmq` (broker) + `redis`
  (result backend); the worker writes papers/figures to `minio` (local S3)
- `flower` is opt-in via `--profile observability`

Postgres / RabbitMQ / Redis are not host-published — only reachable from the
docker network. Migrations run on every `server` start (its command chains
`run_migrations.py` before gunicorn). Public URLs come from `BASE_HOSTNAME`
(top-level `.env`); a Caddy on the host fronts client / API / S3.

For specifics — port bindings, env vars, exact images — read `compose.yaml`
and `compose.override.yaml` directly. To poke at the running stack:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker exec openpaper-server-1 alembic current   # or upgrade head, etc.
docker exec openpaper-postgres-1 psql -U postgres -d openpaper -c '\d <table>'
```
