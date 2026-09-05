# Agentic Chat Refactor — Architecture

Status: design approved for implementation (2026-08-27). Delete this file when the
refactor lands.

## Problem

- Frontend and backend speak different message shapes: the stream is Vercel AI
  UIMessage chunks (hand-assembled in `message_api.py`), history is
  `{role, content, references, bucket}` rows, and the client keeps a third
  in-memory `ChatMessage` shape. Reasoning and tool calls are lost on reload.
- Three divergent chat frontends (PaperChatPanel, ConversationView x2 pages,
  share page) with duplicated citation/markdown rendering; a hand-rolled SSE
  parser that silently drops most protocol events.
- Two divergent backend chat pipelines: single-paper pydantic-ai agent vs.
  legacy `/chat/everything` (Cerebras-hardcoded `gather_evidence` pre-pass +
  compaction/citation-index + non-agentic `chat_with_papers`).
- Model handling is scattered: `_RESPONSES_API_BROKEN_MODELS`,
  `_VISION_UNSUPPORTED_MODELS`, `model.lower().startswith("gpt-")` checks,
  ModelType.FAST/DEFAULT per provider, ad-hoc pydantic-ai model construction.
- The paper's upload-time summary carries `summary_citations` (the "initial
  citation/summary map") with its own remap + click pipeline, duplicated 5x.

## Target architecture

### Single source of truth for messages

- **Ground truth = pydantic-ai `ModelMessage` list**, persisted per assistant
  turn in `messages.bucket.pai_messages` (existing format, unchanged — figure
  bytes stripped/rehydrated as today).
- **Wire format = Vercel AI SDK v6 UIMessage / UIMessage stream**, produced by
  `pydantic_ai.ui.vercel_ai.VercelAIAdapter` (pydantic-ai 1.89.1, already
  pinned). No hand-assembled SSE.
  - Live turn: `adapter.run_stream(...)` → text/reasoning/tool-input/tool-output
    chunks, encoded SSE, header `x-vercel-ai-ui-message-stream: v1`.
  - History: assistant rows' `pai_messages` →
    `VercelAIAdapter.dump_messages()` → UIMessages with the *same parts the
    stream produced* (tool calls with inputs/outputs, reasoning, text).
    Assistant UIMessages from one turn are merged into ONE UIMessage whose id
    is the DB row id. Legacy rows (no dump) degrade to text-only UIMessages.
- The client uses `useChat` (`@ai-sdk/react`, AI SDK v6) with UIMessage as the
  ONLY message type — live and loaded history are indistinguishable.

### Citations

- The prompt-level `---EVIDENCE---` block stays (proven across models), but it
  becomes invisible to clients:
  - A server-side `VercelAIEventStream` subclass (`OpenPaperEventStream`)
    withholds a tail buffer on text deltas and stops emitting once the
    evidence delimiter appears (same holdback trick as the legacy path, moved
    to the protocol layer).
  - `on_complete` (async-generator form) parses the final text's evidence
    block, runs the existing normalizer+LLM reconciliation, then yields
    `DataChunk(type='data-citations', data={citations: [...]})`.
  - History serialization strips the evidence block from the final TextUIPart
    and appends `DataUIPart(type='data-citations', data=references)` from the
    row's `references` column.
- Client: one `data-citations` part per assistant message → Sources UI +
  `[^N]` chip resolution. No client-side evidence stripping.
- Citation dict shape unchanged: `{key: int, reference: str, page?: int,
  paper_id?: str, matched_via?: str}` under `{citations: [...]}`.

### Model customization (`app/llm/models.py`)

```python
@dataclass(frozen=True)
class ModelSpec:
    id: str                      # wire id (Azure deployment name etc.)
    provider: LLMProvider
    display_name: str
    api: Literal["responses", "chat"] = "responses"  # OpenAI-family only
    supports_vision: bool = True
    supports_reasoning_effort: bool = False
    reasoning_summaries: bool = False
```

- `ModelRegistry` builds specs from the existing env lists (`OPENAI_MODELS`,
  `CODEX_PROXY_MODELS`, Anthropic/Gemini presence) merged with a built-in
  capability table (Kimi → api="chat"; DeepSeek → no vision, no effort knob;
  gpt-* → effort + summaries) and an optional `MODEL_OVERRIDES` env JSON for
  dynamic adjustment without code changes.
- `registry.resolve(provider, id)` / `registry.default(role)` (role ∈
  default|fast) replace `ModelType` + `resolve_model*`.
- `build_model(spec) -> pydantic_ai Model` and
  `build_settings(spec, reasoning_effort) -> ModelSettings | None` are the
  ONLY places pydantic-ai models get constructed for chat. Responses API is
  the default; chat-completions only via spec override (broken Azure gateway
  models, chat-only proxies like the codex proxy / Groq / Cerebras).
- `/api/message/models` returns specs **with capabilities** so the client can
  gate the reasoning-effort picker per model dynamically.

### Unified agent runtime (`app/llm/chat/`)

- `runtime.py` — builds the `Agent` + deps and runs it through the adapter;
  shared by both scopes. Usage limits, tool budget, telemetry live here.
- `paper.py` — single-paper scope: existing system prompt build (context
  modes, styles, supplementary papers) + existing toolset (read_section,
  read_pages, search_paper, get_figure, docs tools). Tool functions are
  unchanged.
- `corpus.py` — everything/project scope: same runtime, toolset built from
  `tools/file_tools.py` (search_all_files, read_file, view_file,
  read_abstract) scoped to the user's library or the project's papers.
  **The `gather_evidence` pre-pass, evidence compaction, and citation-index
  resolution are gone from chat** — the agent fetches evidence itself and
  cites with `@cite[n|paper_id|page=?]`. (`evidence_operations.py` itself
  stays: the multi-paper audio overview still uses it.)
- `stream.py` — `OpenPaperEventStream` (evidence holdback) + adapter glue.
- `persistence.py` — history load (bucket → ModelMessages), turn persist
  (user row + assistant row with content/references/pai_messages), GET
  serialization (rows → UIMessage[]).
- `citations.py` — evidence parse + reconciliation (moved from
  paper_agentic_operations; family-aware pymupdf matching unchanged).

### Endpoints (`message_api.py` rewritten)

- `POST /api/message/chat/paper` and `POST /api/message/chat/corpus`
  (replaces `/chat/everything`): body = AI SDK request (`{id, messages,
  trigger}`) + `{paper_id | project_id?, conversation_id, provider, model,
  reasoning_effort?, context_mode?, style?, user_references?}`.
  The client sends ONLY the new user message; server history comes from the
  DB (`message_history=` param — client-sent history is ignored by design).
- `GET /api/conversation/*` responses change `messages` to UIMessage[].
- Legacy deleted: `chat_with_paper` (whole-PDF non-agentic path),
  `chat_with_papers`, chat use of `gather_evidence`,
  `BaseLLMClient.send_message_stream` + provider `send_message_stream`
  implementations (chat was their only consumer). `generate_content` stays
  for non-chat features (rename, audio, data tables).

### Frontend

- Add `@ai-sdk/react`; new `hooks/useOpenPaperChat.ts` wrapping `useChat` with
  `DefaultChatTransport` + `prepareSendMessagesRequest` (last message only +
  body fields). Delete `lib/uiMessageStream.ts`.
- One message renderer `components/chat/ChatMessageList.tsx` rendering
  UIMessage.parts:
  - `text` → markdown + `[^N]` citation chips (CustomCitationLink, kept)
  - `reasoning` → ai-elements `Reasoning` (works on reload now)
  - `tool-*` → compact tool activity row (pretty label client-side from tool
    name+input; expandable input/output) — replaces `data-status` strings
  - `data-citations` → Sources panel (PaperSources for paper scope,
    ReferencePaperCards for corpus scope)
- All four surfaces (paper panel, understand, project conversation, share)
  use the same renderer; page controllers keep their shells but drop their
  bespoke streaming/state code.
- `summary_citations` rendering removed everywhere; overview renders the
  summary markdown with `[^N]` markers stripped. Backend stops generating and
  serving `summary_citations` (column stays; no migration).

### Explicitly out of scope

- Audio overviews (single & multi paper), data tables, discover — untouched.
- No DB migrations.

## SCOPE CUT (user, 2026-08-27)

Only the **paper chat panel** survives this pass. Deleted, to be
reimplemented properly later:

- Understand page + past list, project conversations pages + past list,
  `ConversationView`, `/chat/everything`.
- The share page (page + shared-conversation endpoint + share button). This
  also closes the unauthenticated `bucket` leak.
- The multi-paper audio overview — with it, `evidence_operations.py`,
  `multi_paper_operations.py`, and the legacy file-tools loop lose their
  last consumers and are deleted. Single-paper audio stays.

DB columns are kept (no migrations); only code paths are removed.

## Design revisions from cross-model review (codex + Opus, 2026-08-27)

1. **Share projection (CRITICAL fix)**: the shared-conversation endpoint is
   unauthenticated; its serialization MUST redact — text parts +
   `data-citations` only, no reasoning/tool/step parts.
2. **Holdback state machine**: `OpenPaperEventStream` overrides
   `handle_text_start/delta/end` (PartStartEvent carries initial content).
   Longest-suffix-that-is-a-prefix buffering so a split `---EVIDENCE---`
   never leaks; flush pending tail before text-end; once the marker is seen,
   suppress all remaining text (and whole later text parts) to end of run —
   matching `split_evidence_block` persistence semantics. Adapter subclass
   overrides `build_event_stream`.
3. **Abort/disconnect persistence**: the user row is persisted BEFORE the
   run (with the client UIMessage id as idempotency key in `bucket`);
   the assistant row id is preallocated and passed as `server_message_id`.
   If the stream ends without a run result (disconnect/stop/error), a
   partial assistant row is persisted from the event stream's accumulated
   text with `bucket.interrupted=true`. Duplicate client message ids are
   rejected. **Not implemented: the pg advisory lock** — see the Deferred
   section for why it does not fit this session layer.
4. **Model history ≠ UI pagination**: a dedicated chronological loader
   fetches ALL rows and applies a char-budget policy — newest turns replay
   `pai_messages` dumps; beyond budget, turns degrade to text-only. UI
   pagination stays as-is, separate. (Replay is no longer verbatim: it is
   sanitized for the target model — see revision 17.)
5. **Corpus safeguards**: keep tool budget, result caps + dedup at the
   toolset layer, and a no-evidence fallback message; citation grammar is
   `@cite[n|page=P|paper_id=ID]` (named extras only — the existing parser's
   format).
6. **Registry scope**: chat-only. `BaseLLMClient.generate_content` and its
   ModelType plumbing stay for non-chat consumers (reconciliation FAST
   calls, rename, audio, data tables). Groq/Cerebras are not chat models
   and stay out of the registry.
7. **History serialization details**: drop system/user messages from the
   dump, merge assistant UIMessages in order with `step-start` parts between
   ModelResponses (live-stream parity), scan ALL text parts for the evidence
   block, truncate tool outputs (~6KB marker) in the UI projection, version
   the bucket (`bucket.pai_v=1`), keep text-only fallback for legacy rows in
   BOTH GET serialization and model-history load.
8. **Regeneration**: `trigger=regenerate-message` is rejected (400) for now;
   client does not offer regenerate. Top-level chat `id` must equal
   `conversation_id`.
9. **Operational parity**: keep non-fatal rename, credits refresh + gating,
   `did_chat_message` / error / per-tool-timing telemetry, assistant
   UIMessage id == DB row id via `server_message_id`.

Additional revisions from the Opus review:

10. **Holdback correctness**: `text-start` is emitted lazily on the first
    visible character and `text-end` only for opened parts — the AI SDK
    parser hard-fails on end-without-start. The filter RESUMES after
    `---END-EVIDENCE---` (legacy behavior; suppress-to-end would black-hole
    answers with mid-message evidence). Persisted content uses the same
    `strip_evidence_blocks` semantics so live == reloaded.
11. **on_complete must never raise** (a late reconcile failure would
    convert a delivered answer into a client error) and must be a real
    async *generator*. Citations parse from accumulated raw text, not
    `result.output`. Two `data-citations` chunks share `id='citations'` so
    the reconciled set replaces the raw set in place.
12. **Interrupted runs**: persist in a `finally` with a FRESH DB session
    (the request-scoped one may be torn down on disconnect).
13. **Wire hygiene**: tool outputs are truncated (~6KB preview marker) in
    both the live stream and history serialization; `bucket` is never
    returned by any GET again (it leaks tool transcripts today, including
    on the unauthenticated share endpoint).
14. **Protocol details**: `sdk_version=6` explicitly; `useChat({id:
    conversationId})`; transport uses absolute API URL +
    `credentials:'include'`; `MessageCreate` accepts a preallocated id.
15. **Registry details**: capability table keyed by (provider, id); `xhigh`
    → `high` mapping for chat-completions; reasoning summaries only when
    api==responses; ModelType/BaseLLMClient untouched for non-chat callers.
16. **Server-side enforcement**: add the missing chat-quota check at the
    endpoint (gating is client-only today).

## Reliability layer (cross-model review rounds, 2026-08-29)

17. **Capability-aware replay**: history is model-agnostic on disk but not
    on the wire, so `load_model_history(rows, spec=…)` sanitizes the dump
    for the model about to be called. Two live-verified failures, both of
    which used to poison a conversation permanently for one model while
    another kept working: a figure fetched by a vision model 400s
    DeepSeek/Kimi ("does not support image inputs") — images become a text
    placeholder; and a gpt-5.x `ThinkingPart(id='rs_…')` replayed to a Chat
    Completions model is emitted as a top-level message field ("Extra
    inputs are not permitted") — those ids are cleared. Stored rows are
    never mutated; only the replay copy is.

18. **Transport-level retry**: `RetryingModel` (wrapper Model) retries
    transient provider failures — 3 attempts, 1s/2s backoff, `Retry-After`
    honored and capped. Retrying at the MODEL layer is what makes it safe:
    the agent graph re-sends the full history including executed tool calls,
    so tools are never re-run. Streaming retries only cover failures raised
    while ENTERING `request_stream` (mirrors `FallbackModel`); once a stream
    is yielded, failures propagate. Not retryable: 400/401/403/404/422
    (content filters never succeed on retry).

19. **One attempt budget**: every chat client is constructed explicitly with
    `max_retries=0` and a bounded read timeout (`_pai_compat`), including
    Anthropic and Gemini — otherwise the SDK's own retries multiply with the
    wrapper's (up to 9 HTTP calls per logical request). Non-chat callers
    keep the old behavior via `LEGACY_*`. These clients are per-request and
    provider-unowned, so teardown closes them explicitly.

20. **Chunk delivery is pumped, not pulled**: a retry backoff blocks the
    pull side for seconds, so the protocol stream is drained by a pump task
    into a bounded queue that the endpoint generator consumes; the retry
    callback injects transient `data-retry-status` chunks into the same
    queue (`{state: retrying|recovered, attempt, maxAttempts, delayMs,
    error}`). Teardown is the subtle part: `transform_stream` yields from a
    `finally`, which CONSUMES a delivered cancellation, so stopping the pump
    means cancel + drain + re-cancel under a deadline — and if it still will
    not stop, close the transport and leave the generators to GC rather than
    `aclose()` them under active iteration.

21. **Failed turns are persisted and typed**: `on_error` is the only place a
    mid-run failure is observable (pydantic-ai turns it into an error chunk
    on a 200), so it stashes the text; the row is written BEFORE the error
    chunk reaches the client (a client retrying instantly would otherwise
    duplicate the turn), with `bucket.error` present only for real failures
    — a user Stop stays a neutral interruption. Writes are id-keyed and skip
    an existing row, so a retry after an ambiguous commit cannot duplicate.
    A resubmission reuses the failed turn's rows and deletes the failed
    partial, but never deletes an interrupted row that holds text without an
    error (that is a stop, i.e. a real answer).

## Deferred (known, low-priority — from post-implementation review)

- **Concurrency TOCTOU**: duplicate-submit detection, `max(sequence)+1`
  allocation, the quota check, and the failed-turn reuse/delete are all
  check-then-act without DB locks. The client-id dedupe + dangling-turn
  reuse cover realistic retries; true concurrent multi-tab submits can still
  interleave. **The obvious fix does not work as written**: `crud` commits
  after every operation, so a `pg_advisory_xact_lock` is released at the
  first write of the plan→delete→update sequence; the session-level variant
  survives commits but rides a POOLED connection, so a lock leaked by a
  disconnect/cancellation teardown would block unrelated conversations.
  A real fix needs a non-committing transaction boundary around the whole
  turn setup first.
- **Credit accounting**: still `len(content)/5`; user-reference blocks,
  tool tokens, and model reasoning are unmetered (parity with legacy).
  No reservation, so concurrent requests can both pass the quota check.
- ~~**Figure rehydration**~~ (done): replayed figures are now charged
  against the history char budget before replay is planned (stored S3 size,
  or a conservative estimate when unavailable), and are not fetched at all
  for a model without vision.
- **Read timeout vs. slow first token**: the chat read gap is 180s, and
  pydantic-ai peeks the first event inside `__aenter__`, so time-to-first-
  token counts against it. A reasoning model that thinks longer than that
  without emitting summary deltas is retried 3x and fails after ~9 minutes.
  Still strictly better than the old 600s x 2 SDK retries, but the number is
  a guess until a slow model is measured.

- ~~Nothing here has been exercised live~~ **Done (2026-08-29)**: two E2E
  rounds against the running docker stack verified the retry paths
  (fault-injected 503/400 mock provider: retrying/recovered chunks,
  error chunk, `metadata.interrupted` persisted through real Postgres,
  resubmit replacing the failed row), plus 3 live mid-stream disconnects
  at increasing depths — clean logs, partial turn persisted
  `interrupted=True`, healthy turn afterwards. Untested live: the
  abandoned-pump path (pump swallowing repeated cancels) — unit-only,
  by construction hard to trigger against a real provider.

- **Offset pagination**: "load earlier" pages by offset; new turns shift
  pages. Client-side id-dedup makes overlap harmless, but a sequence-cursor
  API would be correct.
- **Request DB connection** is held (idle-in-transaction) for the stream's
  duration; tools now use their own short-lived sessions, but the request
  session itself still spans the run.

## Testing

- Dev servers (not docker): postgres exposed to host, API on :8003, client
  on :8002 (matches the pre-docker dev env values already in `.env`s).
- Live-call testing uses Azure deployment `DeepSeek-V4-Flash-0731` (cheap,
  exercises the Responses path, no vision/effort so gating is visible).
- Opus 5 agents run the tests (pytest + live smoke + browser); architecture
  and fixes stay with the main thread.
