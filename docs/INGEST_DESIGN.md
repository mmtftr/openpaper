# Paper ingest v2 (rev 5)

Status: implemented (rev 5, 2026-09-24); code contracts in
`server/app/ingest/README.md`. It replaced the `jobs/` PDF pipeline (Celery +
RabbitMQ + Redis + jobs-api + webhook), which has since been deleted; the
sections below keep the design and migration record as written. Personal
single-user deployment: **one**
ingest worker process, one user; designed for readability, not multi-tenant scale.

## 1. Requirements (owner)

| # | Requirement | Where |
|---|---|---|
| R1 | OCR | `ocr` → `ocr_repair` |
| R2 | Metadata from DOI / Crossref / OpenAlex / arXiv; LLM only as a fallback | `metadata` → `metadata_fallback` (§5) |
| R3 | Progress reliably visible in the client and stored in the DB | stage rows are the source of truth (§3) |
| R4 | Features enable progressively; disabled controls show which stage they wait for | feature map + `<FeatureGate>` (§8) |
| R5 | Each stage separate, retries on its own | per-stage status/attempts/backoff (§4) |
| R6 | As parallel as possible | DAG + per-resource concurrency (§2, §4) |
| R7 | Well organised, easy to read | one file per stage (§9) |
| R8 | Shared retry/error primitives, proactive error handling | `app/core/` (§6) |
| R9 | Configurable model per model-using stage | Settings → Models (§7) |

No degraded/early-text mode: features wait for their real inputs. No LLM
summary. AI highlights for every paper upload (not supplementaries).

## 2. Stages

```
 source ─┬─► preview
 (sync)  ├─► text_layer ──► metadata ─────────────────► metadata_fallback (skipped if resolved)
         │        │                                      ▲
         └─► ocr ─┼──► figures                           │
                  └──► ocr_repair ─┬─► outline           │
                     (+text_layer) ├─► highlights        │
                                   └─────────────────────┘
```

A stage runs when all its dependencies are `succeeded` or `skipped`; if one
fails, dependents show `blocked` and resume when it's retried. When `metadata`
resolves a record it marks `metadata_fallback` skipped right away, so
title/authors don't wait for OCR.

| Stage | Needs | Does |
|---|---|---|
| `source` | — | In the upload request: store the PDF in S3, open it with pymupdf (reject corrupt/encrypted), record page count. The 800-page limit is removed. |
| `text_layer` | source | pymupdf text per page + embedded (XMP) metadata |
| `preview` | source | first-page thumbnail |
| `ocr` | source | Mistral OCR in 16-page batches; pages are saved as each batch lands, so a retry only redoes missing pages; progress = pages done |
| `figures` | ocr | render figure boxes at 300 DPI to S3 |
| `ocr_repair` | ocr, text_layer | score OCR against the text layer; vision re-OCR of bad pages (per-page fallback to text layer, as today); writes each page's final text |
| `metadata` | text_layer | find DOI/arXiv id, look up Crossref/OpenAlex/arXiv (§5) |
| `metadata_fallback` | metadata, ocr_repair | skipped if resolved; else retry lookup on OCR text, then LLM extraction ("unverified") |
| `outline` | ocr_repair | today's `paper_outline`, run at ingest instead of on first view |
| `highlights` | ocr_repair, text_layer | 3–5 AI highlights, anchored to PDF rects (port of `jobs/src/highlight_anchor.py`); highlights you've annotated are kept on regeneration |

Supplementaries: source, text_layer, preview, ocr, figures, ocr_repair, outline.

## 3. Data

```
ingest_stages   (one row per stage per paper — this IS the queue and the progress record)
  paper_id, name, status (pending|queued|running|succeeded|skipped|failed|blocked),
  attempt, max_attempts, next_attempt_at, progress_done, progress_total,
  model_used, error_message, started_at, finished_at
ingest_worker   (single row: last_seen — lets the UI say "worker offline")
paper_pages     (paper_id, page_no, text_layer, ocr_markdown, ocr_payload, markdown,
                 markdown_source, ocr_quality)     -- replaces papers.ocr / raw_content / page_offset_map
paper_figures   (paper_id, page_no, ocr_image_id, label, caption, bbox, s3_key)
papers          + doi/arxiv_id/openalex_id, metadata_source per field ('user' = edited by
                  you, never overwritten); drop parser/ocr/raw_content/
                  page_offset_map/upload_job_id after the switch
highlights      + origin ('user' | 'ai')
model_slots     (slot, provider, model, effort)    -- §7
```

S3 keys per paper (`papers/{id}/…`); deleting a paper deletes its prefix.
Figure images are never deleted while the paper exists — saved chat history
refers to their exact keys.

## 4. Worker

- One process: `python -m app.ingest.worker` (same image as the server).
- Picks up due `queued` stages and runs up to N at once per resource
  (cpu 2, OCR 2, LLM 4, network 8) as asyncio tasks; CPU work in a process pool.
- A finished stage saves its output and its status in one transaction, and in
  the same transaction queues any dependents that are now ready — the worker
  starts those **immediately** (no waiting for the next poll), so hops between
  stages add no latency.
- New uploads: the worker polls every 250 ms for freshly queued stages (a cheap
  indexed query), so the first stages start within a fraction of a second.
  Retries with backoff are picked up by the same poll when they come due.
- Failure → the shared classifier (§6): temporary → requeue with backoff
  (5 s, 30 s, 2 min, 10 min); rate-limited → requeue at Retry-After;
  permanent or missing config → `failed` with a readable message.
- Worker start: anything left `running` (crash) goes back to `queued`; every
  30 s the same happens to `running` rows the worker has no task for.
- An outcome write retries while the DB is unreachable (up to 60 s, then the
  result is dropped and the row is requeued by that check). A crashed CPU
  child (segfault/OOM) replaces the process pool and counts as a temporary
  failure. A stage finishing after its paper was deleted sweeps
  `papers/{id}/` (it may have uploaded after the delete).
- API: retry a failed/blocked stage; reprocess a finished stage (resets it and
  everything downstream). Not allowed while that part of the graph is running.

## 5. Metadata

1. Look for a DOI / arXiv id in the PDF's embedded metadata, the filename, the
   source URL, then the first two pages (header area ranked first — body DOIs
   are usually citations).
2. Look it up: DOI → Crossref + OpenAlex; arXiv id → arXiv API; none → title
   search on OpenAlex/Crossref.
3. Accept a record only if its title matches page-1 text and an author surname
   appears on page 1.
4. Fields you've edited are never overwritten.
5. Replaces the current DOI lookups on paper open and in the webhook.

## 6. Shared primitives (`server/app/core/`)

- `errors.py` — one `classify(exc)` → temporary / rate-limited / permanent /
  config, extracted from what `llm/retrying_model.py` already does, and reused by it.
- `retry.py` — backoff policies; SDK retries stay off, short in-call retries
  only for quick idempotent calls, everything longer is a stage retry.
- `deadline.py` — each stage has a time budget; every HTTP/LLM call's timeout
  comes out of what's left.
- `http.py` — shared httpx client (timeouts, Crossref/OpenAlex polite headers).

Proactive: invalid PDFs rejected at upload; a stage whose model/API key isn't
configured fails immediately with that reason (fix it, press Retry); the UI shows
"worker offline" when the heartbeat is stale; every failure has a readable message.

## 7. Model settings

Settings → Models lists every model call site with a provider/model dropdown
(NULL = today's default): `chat.default`, `chat.reconcile`, `chat.title`,
`quick_question`, `discover`, `ingest.ocr_repair` (vision models only),
`ingest.metadata`, `ingest.outline`, `ingest.highlights`, plus the OCR service
model (`ingest.ocr`). Outline and discover follow the default provider's fast
model (on the codex proxy: `gpt-5.6-luna` at effort `max`, since refactor
Phase 6); OCR repair, metadata and highlights stay on OpenAI. Each stage
records the model it used.

## 8. UI

- `GET /api/papers/{id}/ingest` → every stage's status/progress/error + a
  feature map computed on the server:

  | Feature | Needs |
  |---|---|
  | reading, manual highlights, notes | source |
  | title/authors/cite | metadata or metadata_fallback |
  | thumbnail | preview |
  | chat | ocr_repair |
  | figures in chat / viewer | figures |
  | citation jump | text_layer |
  | outline | outline |
  | AI highlights | highlights |

- `useIngest(paperId)` polls every second while something is running, stops when done.
- `<FeatureGate feature="chat">`: disabled controls explain what they're waiting
  for ("Needs OCR — retrying in 18 s") with a Retry button.
- A header popover lists every stage with progress, errors and retry.
- Views refetch when the stage they depend on finishes (SWR key includes that
  stage's `finished_at`).
- Upload opens `/paper/{id}` immediately and **the PDF reader works at once**:
  `source` finishes inside the upload request, so the PDF is in S3 before the
  response returns. Reading, manual highlights and notes never wait for ingest.

Measured today (jobs-worker logs, 2 uploads — indicative only; re-measure at the
end of Phase 5): Mistral OCR ≈ 0.35–0.4 s/page (6.3 s for 18 pages, 13.7 s for
36) plus occasional 429 capacity retries (~9 s once); 4 parallel `gpt-5.5`
metadata calls 8–16 s (title/authors alone 8.7 s); our own overhead ≈ 4–6 s
(≈1 s real work, ≈3 s webhook with serial blocking DOI lookups + passage
indexing, ≤2 s client poll). Total today: nothing visible for ~27–34 s.

Target for the same 18-page paper (to be verified at the end): PDF readable at
0 s; title/authors ~1–3 s (Crossref/OpenAlex); chat + figures ~6–7 s (OCR +
scoring); outline + AI highlights ~15–20 s; server overhead well under 1 s.

Measured (I9 rehearsal, 2026-09-24, restored live DB + disposable MinIO, one
worker; seconds after the upload request): 18-page paper (file upload,
request 0.10 s) — first stages start 0.26, title/authors 1.2 (OpenAlex via
the embedded arXiv id), OCR 7.7 s run (2 batches, ≈0.43 s/page) → chat
8.0, figures 8.7, outline 8.1 (PDF bookmarks), AI highlights 22.0 (one
13.9 s `gpt-5.5` call). 21-page arXiv URL import (request 0.64 s incl.
download) — title 1.3, chat 6.2, figures 6.4, outline 6.3, highlights 22.5.
Stage-to-stage hops ≈ 10–50 ms. The 42 outlines the data migration queues
ran in 54 s (34 from bookmarks, 8 LLM cleanups).

## 9. Code layout

```
server/app/core/        errors.py retry.py deadline.py http.py
server/app/ingest/
  worker.py             entry point + loop
  stages/               source.py text_layer.py preview.py ocr.py figures.py ocr_repair.py
                        metadata.py metadata_fallback.py outline.py highlights.py
  sources/              mistral.py crossref.py openalex.py arxiv.py
  pdf/                  text.py render.py figures.py anchor.py
  features.py api.py models.py
```

Each stage is one small class declaring its dependencies, resource, timeout and
model slot, with a `run()` that returns its output and a `save()` that writes it.

## 10. Migrating existing papers (no credits)

47 of 48 papers have Mistral OCR stored; the other one (`3bc89c7e…`, pymupdf
only, no conversations) is deleted.

1. Backup, stop the old jobs worker.
2. Migration copies `papers.ocr` into `paper_pages` / `paper_figures`:
   - `repair_markdown` only for pages whose source is `openai_ocr_repair`
     (the `openai_ocr` key holds metadata, not text);
   - figure boxes converted using the page's DPI (`figure.dpi` is the render DPI);
   - `pymupdf_fallback` pages → `text_layer`.
3. Stage rows: `succeeded` for everything that exists; `outline` queued for the
   46 papers that don't have one yet (cheap, same as opening each once).
   Existing AI highlights → `origin='ai'`.
4. Replace the search trigger that reads `raw_content`.
5. Check: page and figure counts match, highlights/notes/conversations unchanged.
6. Switch the app over; drop the old columns and the jobs service afterwards.
