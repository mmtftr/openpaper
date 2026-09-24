# OpenPaper personal-only refactor plan (rev 3)

Status: DRAFT rev 3 (2026-09-24), against `pdf-reader-swap` @ `a36f87d`.
Companion design: `docs/INGEST_DESIGN.md`.

**Scope rule:** this is a personal deployment on the owner's tailnet; nobody
else can reach it. No multi-tenant hardening (CSRF, per-request owner checks,
SSRF guards, OTP rate limits, fenced multi-worker queues, …) — only what the
owner asked for plus fixes for real bugs.

## 0. Decisions (owner, 2026-09-24)

- Remove billing/credits (Stripe, plans, subscriptions, limits, metering, abuse detection).
- Remove audio overviews.
- Projects = single-owner folders: remove collaborators, invitations, roles,
  Artifacts (data tables, project audio), forks, sharing. Project-wide chat is
  a later feature.
- Remove marketing surface (landing, blog, legal, about, pricing, subscribed,
  onboarding), trackers, sqladmin, `/finder`, `/graph`.
- Legacy LLM layer → pydantic-ai; every model call site gets a configurable
  model on a Settings → Models page.
- Typed API: FastAPI request/response models → OpenAPI → generated TS client + SWR.
- Ingest rewritten per `docs/INGEST_DESIGN.md`; existing papers migrated, not re-run.
- Order: deletions → small hardening → client fixes + typed API → snapshot → restructure.
  Don't polish what a later phase deletes.

## Phase 0 — Prep
- Branch `personal-cleanup` off `pdf-reader-swap`; `pg_dump` + bucket mirror.
- Add `scripts/smoke.py`: against the running stack, open a known paper
  (PDF, figure, outline), one chat turn with a citation, create/delete a
  highlight + note. (`smoke_chat.py` was deleted in `0977058`; there's no
  end-to-end check today.)
- Record baseline: pytest, `tsc`, `next build`, compose build, smoke.
  Recorded 2026-09-24 @ `57a527d`: pytest 591 passed; `tsc` clean; `next build`
  ok; compose build (server, client) ok; smoke all passed. Backups in
  `~/backups/openpaper-2026-09-24/` (`openpaper.dump`, `bucket/`).

## Phase 1 — Deletions

Each deletion takes its consumers with it (imports, ORM relationships, response
fields, client types in `lib/schema.ts`, nav links, env vars, tests) so every
step stays green. sqladmin goes first because it imports every model.

**1a. sqladmin + repo junk.** `database/admin.py` + `setup_admin`;
`AGENTIC_CHAT_REFACTOR.md`, `REPO_INSPECTION_FEATURE.md`, `alphaxiv-tools.json`,
`scripts/probe_mistral_ocr.py`, `server/lr_research_diagram.png`, `server/evals/`
(broken), `app/scripts/test_*.py`, `backfill_paper_passages.py`, stray `.pyc`.
Keep `benchmarks/` and `client/src/app/(benchmark)/`.

**1b. Billing / onboarding / marketing.**
- Server: `api/subscription/*`, `subscription_limits.py`, `subscription_crud.py`,
  `Subscription`; onboarding API/CRUD/model; `abuse_detection.py`; `scrape.py`;
  subscription lookup in `auth/dependencies.py` and `auth_api.py`; all
  `can_user_*` gates and plan-based model forcing in `chat/runtime.py` /
  `chat/quick_question.py`; credit metering (`message_crud` sums,
  `chat_usage_crud.py`, `ChatUsageEvent`); billing/onboarding emails (keep OTP
  email); `stripe`, `firecrawl-py` deps and env vars; flower service.
- Client: pricing, subscribed, `(home)`, `(blog)`, `(legal)`, about,
  `src/content/`, MDX setup, blog prebuild, `BlogPostToast`, `sitemap.ts`,
  `OPOnboarding`, `useSubscription` + usage cards/limit toasts/upgrade links,
  `@stripe/*`, confetti, analytics provider + Plausible/Google Ads scripts.
  Unauthenticated `/` → `/login`.
- Migration: drop `subscriptions`, `onboarding`, `chat_usage_events`.

**1c. Audio** (0 rows). Server audio API/task/speech/CRUD/models/prompts, the
audio join in `project_crud.py`; client audio components, Audio tab/`rsf` value,
`ProjectCard` audio counts. Migration: drop `project_audio_overview`, then
`audio_overviews`, `audio_overview_jobs`.

**1d. Multi-user → folders.** Rename `project.admin_id → owner_id`; delete
collaborators/invitations/roles (API, CRUD, models, client components, role
gating in `ProjectCard`), data tables (API, CRUD, webhook, jobs task, client),
project conversations, forks (`/fork`, `fork_paper`, `duplicate_file_from_url`),
sharing (routes, public helpers, `is_public`/`share_id`), `/api/auth/admin/block`
+ `is_blocked`, Google OAuth (unconfigured), `/projects/create` page (use the
dialog). Migration: drop data tables (rows → results → jobs), role tables,
`papers.is_public/share_id/parent_paper_id`, `users.is_blocked`, the second user
(0 papers), orphan `'everything'`/project conversations; one conversations CHECK
allowing only `paper`.

**1e. Superseded server code.** `PaperNote`, `PaperImage` (+ summary
placeholder rewriting on paper detail), `paper_passages` (+ trigger function),
`summary`/`summary_citations`/`starter_questions` and their UI, MRU conversation
route, share markdown/outline routes, `/search/local/stats`, `/api/auth/topics`,
`track_event` dead params, the second `declarative_base()`. Local search itself
stays as is until Phase 5.

**1f. Superseded client code.** `/finder` (keep `/api/search/global` — citation
resolution uses it), `/graph` + `CitationGraphButton`, `Annotation.tsx`,
`PaperImageView`, `PaperMetadata`, `TopicBubbles`, `magicui/*`,
`inline-citation.tsx`, unused `ui/*`, `promisePolyfill.ts`, dead hook state,
`handleStatusChange`, duplicate `useMobile`, merge `components/hooks/` into
`src/hooks/`, trim `prompt-input.tsx`/`message.tsx`, unused deps.

**1g. Legacy LLM → pydantic-ai.** Move `LLMProvider`, `_parse_models_env`,
`ModelOption` out of `provider.py`; add `model_slots.py` (env defaults that
reproduce today's per-call-site choice) and `oneshot.complete(slot, prompt,
output_type=…)`; port rename, citation reconcile, outline, discover; delete
`provider.py`, `base.py`, `operations*.py`, `conversation_operations.py`,
`json_parser.py`, `retry_llm_operation`, GROQ/Cerebras, evidence-pipeline
leftovers, legacy tool dicts, `_pai_compat` legacy shims; one `chat/evidence.py`.

## Phase 2 — Small hardening

Only items that matter with a single user on a tailnet:
- ~~Logfire: stop capturing request/response bodies and prompts.~~ Reverted at the
  owner's request (2026-09-24): bodies, prompts and outputs stay in Logfire for
  reviewing transcripts.
- Two jobs bugs that lose uploads until Phase 5 replaces the pipeline: the
  misspelled `task_reject_on_worker_lost` (`jobs/src/celery_app.py:44`) and
  `result: Optional` on the paper webhook (failures currently 422).
- `search_paper` regex timeout (a bad model regex can hang chat tools).
- gunicorn workers from env (2–3 instead of 19; ~5 GB RSS today).

## Phase 3 — Client fixes + typed API

Skip the upload/ingest UI (Phase 5 replaces it).

- **Server:** request + response models on every route, one `ApiError`
  shape, typed SSE data parts, typed highlight positions; blocking async
  handlers → `def`; `pyright` + `ruff`; `model_slots` table +
  `GET/PUT /api/settings/models`.
- **Client:** `openapi-typescript` + `openapi-fetch` + SWR hooks, `yarn gen:api`
  with a staleness check; replace `fetchFromApi`, `repoApi`, the quick-question
  SSE parser and most of `lib/schema.ts`; Settings → Models page.
- **App shell:** one root layout (`next-themes`), `error.tsx`, middleware
  redirect to `/login`, SWR everywhere.
- **Bugs:** persistent chat across tab switches (external `Chat` instance) with
  document-revision refresh for agent doc writes (replaces `paperDocEvents`);
  highlights scoped to the displayed paper; citation jump `{term, page, nonce}`;
  composer split + memoised messages; one markdown renderer (Streamdown);
  autosave 409 handling; `LibraryTable` error crash + bulk-delete confirm;
  discover abort; ligature/hyphen fix moved into `findMatchRange` and the dead
  matcher deleted.

Phase 3 as done (2026-09-24): SWR covers load-on-mount reads; the paper page's
paper/displayed-paper loading, the notes editor's document loads, auth, the
reader outline fallback and the chat conversation list stay imperative typed
calls (they drive redirects, save/conflict state or create-if-empty logic) —
revisit in the Phase 5 client decomposition. "One markdown renderer" = chat,
citations and quick question on Streamdown; the paper markdown *reader* stays
on Milkdown/Crepe like the notes editor. Alembic history was squashed into one
baseline instead of keeping the per-deletion migrations (owner's call).

## Phase 4 — Snapshot
Commit on `personal-cleanup`, tag `pre-restructure`, branch `restructure` off it.
Before branching: one codex gpt-6-astra review of the whole cumulative diff
(`57a527d..pre-restructure`), findings filtered by the scope rule and fixed.
Done 2026-09-24: review found a notes-conflict overwrite on editor unmount/doc
switch, crash-cap uploads recorded as Celery success, and duplicated live
questions on "load earlier" — all fixed before tagging.
After Phase 5, merge into `master`.

## Phase 5 — Restructure
- **Ingest v2** (`docs/INGEST_DESIGN.md`), then delete `jobs/`, RabbitMQ,
  Redis, jobs-api, webhooks, `paper_upload_jobs`, legacy paper columns, the
  upload polling loops; compose gains `ingest-worker`.
- **Chat runtime:** split `run_paper_chat` into plan / store / stream pump shared
  with quick question; one `WrapperToolset` for tool budgets; split `history.py`.
- **Data layer:** SQLAlchemy 2.0 typed models split by domain; CRUD raises
  instead of swallowing; `pydantic-settings`; split `paper_api.py` / `paper_crud.py`.
- **Client:** decompose `PaperChatPanel` and `paper/[id]/page.tsx`; paper-level
  jotai store; shared note-thread component.
- **Ops/docs:** client standalone output; rewrite `README.md`, `DEVELOPMENT.md`,
  `CLAUDE.md`.

## Phase 6 — Codex-proxy fast model (after the refactor is otherwise done)

Context (verified by the owner 2026-09-24): the proxy (`LLMProvider.CODEX_PROXY`,
`~/.local/bin/codex-raycast-proxy`) rejects `gpt-5.4-mini`, the current
`CODEX_PROXY_FAST_MODEL`, so every FAST call routed to it fails.
`gpt-5.6-luna` works. The proxy now forwards Chat Completions
`reasoning_effort` to the Codex Responses backend (luna accepts
low/medium/high/xhigh/max; restart with
`launchctl kickstart -k gui/$(id -u)/com.mmtf.codex-raycast-proxy`), and emits
stream usage when `stream_options.include_usage` is set (pydantic-ai sets it).

1. `CODEX_PROXY_FAST_MODEL=gpt-5.6-luna` in `server/.env`, `.env.example` and any
   doc that names the fast model; `docker compose up -d --force-recreate server`.
2. FAST calls on the codex proxy run at reasoning effort `max`. Check where the
   refactored code builds model settings: the old `ModelRegistry.build_settings`
   mapped `xhigh`→`high` for Chat-Completions specs (the proxy is `api="chat"`);
   that mapping must not clamp or drop `max`/`xhigh` for the codex proxy. Also
   confirm pydantic-ai's OpenAI chat settings accept the literal `max`. The slot
   settings API (`/api/settings/models`, `ModelSlot.reasoning_effort`) and the chat
   request's `reasoning_effort` Literal currently allow only low…xhigh — add `max`. Verify
   with a live call that reasoning tokens are non-zero.
3. Remove the workaround: every FAST slot in `app/llm/model_slots.py`
   (`chat.title`, `chat.reconcile`, `discover`, `ingest.outline`) is pinned to the
   OpenAI/Azure provider only because the proxy's fast model was broken → unpin
   them so they use the default provider again. Re-run a live outline generation + the outline tests.
4. `CLAUDE.md` codex-proxy section: drop the "It rejects gpt-5.4-mini … routes to
   Azure explicitly" bullet; correct "reasoning_effort all verified live" — the
   proxy silently ignored `reasoning_effort` until 2026-09-24 and now forwards
   it; drop the "never sends token usage on streaming responses" bullet if the
   usage check in step 5 confirms it's fixed.
5. Server test suite; one live **streamed** chat on the proxy and one FAST
   structured-output call; report input/output tokens and reasoning tokens
   observed (streamed usage should now be non-zero in the refactored chat path).

## How each phase is executed

- **Fan out:** each phase's sub-steps go to parallel Opus agents (Agent tool;
  not Fable) with disjoint file ownership — separate git worktrees where the
  sub-steps touch overlapping files — and the main session integrates.
- **Review after each phase:** one codex gpt-6-astra review of the phase diff
  (plus an Opus review per the global review-checkpoint rule).
- **Scope guard:** review findings are filtered against this plan's scope rule
  before anything is adopted — real bugs and consistency errors in the change
  are fixed; new hardening or new features are not added. Anything
  borderline goes to the owner as a question, not into the plan.

## Checks per phase
pytest, `tsc`, `next build`, compose build + restart, smoke script,
`alembic upgrade head`, quick browser pass; codex + Opus review of each phase diff.
