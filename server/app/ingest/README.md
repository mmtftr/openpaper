# app/ingest — contracts (design: `docs/INGEST_DESIGN.md`)

**Data** (`models.py`, migration `ingest_v2_20260926`)
- `IngestStage` — PK `(paper_id, name)`; `status` ∈ `StageStatus`; the queue:
  due = `status='queued' AND next_attempt_at <= now()` (partial index
  `ix_ingest_stages_due`). `attempt` counts attempts started. `error_message`
  holds the last failure (or the skip reason); `error_kind` ∈ `ErrorKind`.
- `IngestWorker` — single row `id=1`, heartbeat `last_seen`; stale after
  `WORKER_STALE_AFTER_SECONDS`.
- `PaperPage` — `(paper_id, page_no)` 1-based. `text_layer` ← text_layer;
  `ocr_markdown` + `ocr_payload` ← ocr; `ocr_quality`, `repair_markdown`,
  final `markdown` + `markdown_source` (`MarkdownSource`) ← ocr_repair.
- `PaperFigure` — `bbox` in PDF points, top-left origin `{x0,y0,x1,y1}`;
  unique `(paper_id, page_no, ocr_image_id)` (Mistral ids restart per batch).
- `Paper.metadata_source` — `{field: MetadataSource}`; `"user"` = edited by
  the owner → ingest never writes that field. `Paper.arxiv_id/openalex_id`.
- `Highlight.origin` — `"user"` | `"ai"`. (Legacy `role` = `"assistant"` for
  AI highlights; set both until `role` is dropped.)

**Graph** (`graph.py`, pure) — `NEEDS`, `STAGES` (topological),
`SUPPLEMENTARY_STAGES`, `LABELS`; `stages_for(is_supplementary)`,
`dependents_of`, `upstream_of`, `downstream_of` (reprocess / block set),
`ready_after(statuses)` → pending/blocked rows whose needs are all
succeeded/skipped, `blocked_by(failed, statuses)`, `is_done(status)`.

**Stages** (`stages/<name>.py`, one class each; `registry.get_stage(name)`)
- Class attrs: `name`, `needs`, `resource` (`config.Resource`), `timeout_s`,
  `max_attempts` (5), `model_slot`, `applies_to_supplementary`,
  `runs_in_request` (only `source`: the upload request runs it inline).
- `async run(ctx) -> Output` does the work, no DB writes (except OCR saving
  finished batches so retries resume, via `ctx.write`). Must be safe to
  re-run.
- `save(session, ctx, output)` writes the output inside the worker's
  transaction that also marks the stage succeeded and queues ready
  dependents. Never commit. `metadata.save` marks `metadata_fallback`
  skipped when it resolved a record.
- `skip(reason)` → skipped; `fail_permanent(msg)` → failed, no retry.
  Other exceptions go through `core.errors.classify`.
- `check_config()` runs before `run()`; raise `ConfigError` for a missing
  key/model (default: the model slot must resolve; `ocr` checks Mistral).
- `resolve_model(ctx)` → the slot's model, recorded in `ctx.model_used`.
- `StageContext`: `paper_id`, `stage`, `attempt`, `is_supplementary`,
  `deadline` (`Deadline`; take every call's timeout from
  `deadline.timeout(cap)`), `await progress(done, total)`,
  `await read(fn)` (read-only session in a thread), `read_session()`,
  `await write(fn)` (own short committed transaction — OCR batches only),
  `await cpu(fn, *args)` (process pool in the worker), `get_s3()`, `log`.

**Worker duties** (`worker.py`, to write): poll every
`config.POLL_INTERVAL_SECONDS`; run up to `config.CONCURRENCY[resource]`;
`asyncio.timeout(stage.timeout_s)`; on failure `classify` then
`core.retry.next_stage_delay(classified, attempt, max_attempts)` → requeue
at now+delay, or `failed` + `graph.blocked_by`; on start, `running` →
`queued`; heartbeat every `HEARTBEAT_INTERVAL_SECONDS`.

**Features** (`features.py`) — `features({name: row_or_status})` →
`{feature: FeatureState(enabled, waiting_on, cause, reason)}`; `cause` is the
stage to show/retry (a failed upstream stage first).

**Core** (`app/core/`) — `errors.classify(exc) -> Classified(kind, message,
retry_after)`, `ConfigError`/`PermanentError`/`TemporaryError`;
`retry.STAGE_BACKOFF`, `retry.retry_call`; `http.shared_client()`.
