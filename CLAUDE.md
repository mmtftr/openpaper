# CLAUDE.md

## Deployment shape

Everything runs via Docker Compose on this host. `compose.yaml` is canonical;
`compose.override.yaml` overlays the `paper.example.com` deploy (HTTPS, secure
cookies, S3 served behind Caddy at `/s3/...`). Compose merges them
automatically — `docker compose up` brings up the production-ish setup.

Containers and roles:
- `client` (Next.js) → `server` (FastAPI/gunicorn) → `postgres`; PDFs,
  previews and figure images live in `minio` (local S3) under `papers/{id}/`
- `ingest-worker` (`python -m app.ingest.worker`, same image/env as `server`)
  processes uploads stage by stage (`docs/INGEST_DESIGN.md`); the queue and
  progress are the `ingest_stages` rows, its heartbeat is `ingest_worker`.
  The upload request itself stores the PDF and queues the stages, so the
  reader works before the worker picks anything up.

Postgres is not host-published — only reachable from the docker network.
Migrations run on every `server` start (its command chains
`run_migrations.py` before gunicorn); `ingest-worker` waits for the server
to be healthy. Public URLs come from `BASE_HOSTNAME` (top-level `.env`); a
Caddy on the host fronts client / API / S3.

For specifics — port bindings, env vars, exact images — read `compose.yaml`
and `compose.override.yaml` directly. To poke at the running stack:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker exec openpaper-server-1 alembic current   # or upgrade head, etc.
docker exec openpaper-postgres-1 psql -U postgres -d openpaper -c '\d <table>'
docker logs -f openpaper-ingest-worker-1          # stage runs, retries, errors
```

`server/.env` changes require recreating the container, not just restarting
it — `docker compose restart server` reuses the env baked in at container
creation, so it silently keeps the old values:

```bash
docker compose up -d --force-recreate server ingest-worker
```

The server image installs exactly what `server/uv.lock` pins (`uv export
--frozen` in `server/Dockerfile`), not a fresh resolve of `pyproject.toml`.
Resolution is capped by `[tool.uv] exclude-newer` in `server/pyproject.toml`
(a release-age cooldown) — a dependency bump needs that date moved forward
and `uv lock` re-run, and never a date younger than about a week.

## Azure OpenAI model deployments

`OPENAI_MODELS` (`server/.env`) lists the Azure-backed models shown in the
chat model picker when `AZURE_OPENAI=true` — the picker also shows the
`CODEX_PROXY` provider's models, grouped separately (see below). Its ids
must be actual **deployment names**
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

## The codex proxy as a second chat provider

`LLMProvider.CODEX_PROXY` (`CODEX_PROXY_*` in `server/.env`) is the local
`codex-raycast-proxy` on `127.0.0.1:8788`, reached from containers at
`host.docker.internal:8788` and authenticated by the ChatGPT/Codex
subscription. It runs **alongside** Azure — overlapping ids stay
independently selectable because the client sends `{model, llm_provider}`
together.

It is currently the default (`DEFAULT_LLM_PROVIDER=codex_proxy`) because
**`gpt-6-astra` is not deployed on the Azure resource** but the proxy serves
it. Non-obvious facts about that proxy:

- `gpt-6-astra` does **not** appear in its `GET /v1/models` list yet works
  (streaming, tools, image input, `reasoning_effort` all verified live) — so
  that list is not proof a codex model is unavailable.
- It **ignores `response_format` json_schema** (returns prose). Structured
  output still works only because pydantic-ai defaults to tool-output
  (`final_result` tool); don't move any caller to native JSON-schema output
  while this provider is the default.
- It **never sends token usage on streaming responses** (it keeps usage from
  `response.completed` but only adds it to non-streaming bodies, even with
  `stream_options.include_usage`), so streamed chats on this provider record
  0 tokens. Non-streaming calls report usage. The fix belongs in the proxy
  (`~/.local/bin/codex-raycast-proxy`), not the server.
- It rejects `gpt-5.4-mini`, so every fast-model slot in
  `app/llm/model_slots.py` (`chat.title`, `chat.reconcile`, `discover`,
  `ingest.outline`) is pinned to the Azure/OpenAI provider.

`DEFAULT_LLM_PROVIDER` is read by the `ModelRegistry`; slots without a
pinned provider (`chat.default`, `quick_question`) follow it. The ingest
worker's slots (`ingest.*`) are pinned to Azure/OpenAI, as the old jobs
service was, unless Settings → Models overrides them.
