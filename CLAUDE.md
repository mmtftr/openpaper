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

`server/.env` changes require recreating the container, not just restarting
it — `docker compose restart server` reuses the env baked in at container
creation, so it silently keeps the old values:

```bash
docker compose up -d --force-recreate server
```

## Azure OpenAI model deployments

`OPENAI_MODELS` (`server/.env`) lists the models shown in the chat model
picker when `AZURE_OPENAI=true`. Its ids must be actual **deployment names**
on the `your-azure-resource` Azure resource, not upstream model names —
Azure lets a deployment be named anything, and this resource's deployments
happen to reuse the upstream model name for OpenAI's own models (`gpt-5.5`,
`gpt-5.4-mini`, ...) but not for third-party ones (`FW-Kimi-K3`,
`DeepSeek-V4-Flash-0731`).

**Do not use `GET {AZURE_OPENAI_ENDPOINT}/models`** (the data-plane
`/openai/v1/models` route) to find what's usable — it returns the entire
regional model *catalog* (hundreds of entries: Claude, DeepSeek, Llama,
Phi, ...), not what's actually deployed on this resource. Calling a model id
from that list 404s with `"The API deployment for this resource does not
exist"` unless it's separately been deployed.

The list of what's actually deployed comes from the AI Foundry project
management API instead (same API key, `Authorization: Bearer`, needs an
explicit `api-version` — `2025-04-01-preview` 400s, `2025-05-01` works):

```bash
curl -s "https://your-azure-resource.services.ai.azure.com/api/projects/your-azure-resource_project/deployments?api-version=2025-05-01" \
  -H "Authorization: Bearer $AZURE_OPENAI_KEY" | jq '.value[] | {name, modelPublisher, chat_completion: .capabilities.chat_completion}'
```

Only entries with `capabilities.chat_completion == "true"` are usable for
chat (e.g. `gpt-5.4-pro` is deployed but chat-disabled). Non-OpenAI
deployments (Fireworks/DeepSeek/Anthropic passthroughs) are reachable
through the same OpenAI-compatible `/openai/v1/chat/completions` and
`/openai/v1/responses` endpoints as the native models — verify a new one
with a live call before adding it to `OPENAI_MODELS`, since low-capacity
deployments (e.g. Fireworks-hosted ones) can 429 under light load.
