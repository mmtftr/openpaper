# Open Paper (personal deployment)

A single-user deployment of [Open Paper](https://github.com/sabaimran/openpaper),
a research-paper reader with an AI copilot, running on one host and reached
over the tailnet at `https://paper.example.com`.

What it does:

- **Reader**: a pdf.js PDF view (or the OCR'd markdown) with outline,
  thumbnails, figures, supplementary files, and bibliography hover cards that
  resolve references against your library, Crossref, OpenAlex and arXiv.
- **Chat with citations**: per-paper chat whose answers cite passages you can
  click to jump to. It can also explore the paper's companion GitHub repo in a
  sandbox, and "quick question" answers questions about a selection in the
  repo's code viewer.
- **Annotations and notes**: highlights with note threads (plus AI-suggested
  highlights) and a markdown notes document per paper.
- **Library, projects, discover**: tags and search across your papers,
  single-owner project folders, and an OpenAlex/Exa-backed discover search.
- **Ingest**: uploads (PDF file or URL) are processed in stages by a
  background worker: OCR, metadata, figures, outline, AI highlights. Each
  feature switches on as soon as the stage it needs is done.
- **Model slots**: every LLM call site (chat, quick question, titles, discover,
  each ingest stage) uses a named slot, and you can change each slot's
  model on **Settings → Models**.

## Layout

| Path | What |
|---|---|
| `server/` | FastAPI app and the ingest worker (same code and image). Python 3.12, uv, pydantic-ai, SQLAlchemy, Alembic |
| `client/` | Next.js 15 (App Router) app, standalone build, with a typed API client generated from the server's OpenAPI |
| `compose.yaml` | postgres, minio (local S3), server, ingest-worker, client |
| `compose.override.yaml` | the `paper.example.com` overlay: HTTPS URLs, secure cookies, S3 under `/s3/` |
| `docs/INGEST_DESIGN.md` | ingest v2 design; `server/app/ingest/README.md` has the code contracts |
| `scripts/` | `smoke.py` (end-to-end check), `rebuild.sh` (rebuild, restart, prune) |
| `benchmarks/` | reader citation-hover and annotation-jump benchmarks |

## Running it

```bash
cp .env.example .env                  # BASE_HOSTNAME etc. for compose
cp server/.env.example server/.env    # API keys, models
docker compose up -d --build
```

See [DEVELOPMENT.md](./DEVELOPMENT.md) for configuration, host dev servers,
checks and migrations.

## License

AGPL-3.0, see [LICENSE](./LICENSE).
