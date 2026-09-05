# Repo Inspection (Monty sandbox) — design notes

Status: **implemented and deployed** (2026-08-27, uncommitted on
`pdf-reader-swap`). Feasibility trials live in `spikes/monty-deepseek/`
(see its `REPORT.md`); the plan below was cross-reviewed (codex + Opus)
pre-build, implemented per the binding revisions, and both sides were
cross-reviewed again post-build. Delete this doc when the branch lands.

Post-v1 additions built the same day (not in the plan below):
- **Quick question**: `POST /api/message/quick-question/code` — ephemeral
  tool-less streamed answer about a selected code range; context =
  adaptive-mode paper preload + full file (≤64KB/2000 lines, else
  selection-centered window) + selection. No persistence. Client panel
  in the code viewer off the selection toolbar.
- **Select-to-attach**: viewer selections attach to chat via the
  existing `user_references` channel as `{path} lines {a}-{b}:` strings.
- **Line-wrap toggle** in the viewer (persisted, also governs chat code
  blocks).
- **Quote vs reference** is model-driven: the repo prompt instructs
  fenced-code-block quoting in prose when showing code helps, citations
  as compact pointers always; the client renders citations compactly
  (no code in sources).
- Erratum: quota exhaustion returns **403** (matching `/chat/paper`),
  not the 429 the quick-question contract draft said; clients treat
  both as quota.

## Vision

The paper-chat agent gains code inspection of a paper's companion GitHub
repo. The repo is pulled into a **Monty** virtual filesystem
(`pydantic-monty` — pydantic's sandboxed Rust Python interpreter) and the
agent gets one tool, `run_python(code)`, executing in a persistent
sandbox session with the repo mounted read-only at `/repo`. The model
writes Python to explore, search, and reason about the code.

Why Monty: microsecond startup (no container), hard resource limits
(memory / wall time / recursion), no default filesystem or network access,
read-only directory mounts, restricted-but-sufficient stdlib subset
(`pathlib`, `open`, `re`, `json`, `itertools`, `collections`, ...),
persistent state across `feed_run` calls within a session.

## Decisions so far

### 1. Prelude of host-side helpers (decided: yes)

Reliability and round-trip economy beat raw expressiveness for weak
models. Expose a small documented set of **host functions** inside the
sandbox via Monty's `external_lookup` (they execute host-side at native
speed; the model composes them with its own Python in a single
`run_python` call — no extra tool round-trips):

- `tree(path='/repo', max_depth=3)` — directory tree, sizes
- `read(path, start=1, end=None)` — line-numbered contents (capped)
- `grep(pattern, path='/repo', glob='*', context=2, max_results=50)` —
  rg-style `file:line:` output with context

Raw Python stays the escape hatch for the long tail (counting,
signature extraction, cross-referencing). Deliberately NOT separate
pydantic-ai tools: that would break in-sandbox composition.

Trial A/B arm (bare vs prelude) measures the actual effect on tool-call
count, error rate, and correctness for DeepSeek.

### 2. Code citations → GitHub permalinks (decided: yes)

Extend the existing evidence-block protocol with code extras:
`@cite[n|file=path/from/repo/root.py|lines=142-156]`. The streaming
holdback filter is content-agnostic inside the brackets — no changes to
the hardened stream path; only `CitationHandler.parse_evidence_block`
learns the new extras.

- At ingestion, resolve branch → **commit SHA** and store it with the
  snapshot. Citations render as
  `github.com/{owner}/{repo}/blob/{sha}/{path}#L{a}-L{b}` — permanent,
  shows exactly what the agent read.
- **Verification is host-side, no LLM**: we hold the ground-truth file
  bytes, so check the quoted snippet appears at the claimed lines;
  repair line numbers by searching the file when the quote is real but
  the range is off. (Contrast: PDF citations need OCR↔pymupdf
  reconciliation + FAST-model fallback.) Trial measures exact /
  repairable / fabricated citation rates per model and arm.

### 3. Code viewer popup + syntax highlighting (decided: yes)

Client has NO highlighting today (chat markdown = `AnimatedMarkdown.tsx`
→ react-markdown + remark-gfm/math + rehype-katex, `components`
override map available; no shiki/prism/monaco deps). Plan:

- `lib/shiki.ts` — lazy singleton highlighter, small grammar set
  (python, ts/js, json, yaml, toml, md, shell, c/cpp, rust, go; plain
  fallback), dual themes following app light/dark.
- `CodeBlock` component in the react-markdown `components` map for
  fenced blocks in chat. Streaming-aware: render plain `<pre>`
  immediately, highlight debounced / once the block stops growing.
- `CodeViewerDialog` (shadcn Dialog + ScrollArea), two panes:
  - **Left: repo file tree** — collapsible directories built from the
    snapshot manifest, file sizes, quick filter input for jump-to-file
    (reuse the cmdk `command.tsx` pattern). No virtualization needed
    (snapshot is text files only, capped count).
  - **Right: file view** — line-numbered highlighted content,
    scroll-to + highlight the cited range, header with path, "Open on
    GitHub" SHA permalink, copy button. Degrades on big files (byte
    cap, plain text past threshold).
  Opening from a citation preselects that file at the cited lines; the
  user can then browse the whole repo freely.
- **Data source is our snapshot, not GitHub**:
  `GET /api/paper/{paper_id}/repo/tree` (manifest: paths + sizes +
  commit SHA, written once at ingestion) and
  `GET /api/paper/{paper_id}/repo/file?path=...` serve the ingested
  SHA-pinned tree — the user sees the exact bytes the agent read;
  private-repo support stays possible later. GitHub is a secondary
  external link only.
- Entry points: (a) code-citation click → viewer at cited lines;
  (b) tool-activity file chips — prelude host functions record which
  paths `read()`/`grep()` touched and report them in tool-call
  metadata; client renders clickable chips ("read model.py") on the
  tool rows.

### 4. Repo snapshot persistence

Ingestion: GitHub tarball (codeload, public repos) → filter (text/code
only, size cap per file, skip .git) → store the tree in minio/S3 keyed
by paper_id + commit SHA. The sandbox mounts (or is seeded from) this
snapshot on every chat turn — no per-message re-download. Same snapshot
backs the viewer endpoint. Repo association: paper → repo URL (from
paper metadata/user input; exact UX TBD).

### 5. Deferred (explicitly out of scope now)

- **Docs querying** (library documentation lookup for the agent).
  Architecturally a drop-in later: another read-only mount (docs tree)
  or one more prelude host function. Do not design around it now.
- Private repos / auth'd fetch.
- Write access / running the repo's own code (Monty can't import
  third-party libs anyway — this is *reading* infrastructure).

## Trial results (spikes/monty-deepseek/, answered)

**VERDICT: GO.** DeepSeek-V4-Flash-0731 completed all four nanoGPT tasks
with ZERO sandbox errors across every run (4–19 tool calls each); it
cited file+line naturally. Timeouts occurred only on the 2,355-file
pydantic-ai repo under the spike's 300s cap, with the model still
progressing error-free — scale/latency, not capability. Prelude's
clearest win: cross-file trace T3, 92s/16 calls bare → 29s/8 calls.

Facts settled by the spike (details in spikes/monty-deepseek/REPORT.md):

- Python `pydantic-monty` (0.0.21) **supports directory mounts**
  (`MountDir`, keyword-only, read-only mode enforced). Gotcha: the
  mount is **per-feed** — pass it on every `feed_run` or reads raise
  `PermissionError`. `external_lookup` handles large return values fine
  (1.5 MB string ok) → prelude via host functions works.
- The interpreter subset is much smaller than Monty's README implies:
  NO `glob`/`Path.rglob`, `os.walk`, `os.path`, `functools`, `io`,
  `textwrap`, generators/`yield`, `match`, class inheritance,
  `str.format`, `json.load` (only `loads`), `dir()`/introspection.
  f-strings, `re`, `pathlib` basics, comprehensions, dataclasses,
  plain classes all work. Hand-rolled walk over 2,269 files: 0.12s —
  perf is a non-issue.
- **The sandbox is not introspectable from inside** (no `dir`,
  `getattr` on methods raises) → the system prompt must accurately
  list the subset; it's the model's only source of truth.
- **`max_duration_secs` is a cumulative session budget, and exhausting
  it (or memory) permanently poisons the session** — every later feed
  fails instantly. Production wrapper needs: pool `request_timeout` as
  the real per-call deadline (~25s), poison detection (limit error in
  ~0ms) + transparent session rebuild with an explicit "session RESET,
  redefine your variables" notice in the tool output. Implemented and
  verified in spikes/monty-deepseek/sandbox.py.
- Error messages are CPython-quality tracebacks with carets — models
  iterate on them well (zero unrecovered errors observed).
- Ingestion: file-size cap must be ~1 MB, not 200 KB (200 KB silently
  dropped pydantic-ai's 212 KB `agent/__init__.py` — invites
  confabulation). Size the mount `memory_usage_limit` against the
  ingested tree (default 100 MB is too small for big repos).
- `CodeModeToolset` does NOT exist in pydantic-ai 1.89.1 (vestigial
  comments only) — hand-rolled `run_python` tool, no version bump.
- Reusable spike code: sandbox.py (poison-safe wrapper), helpers.py
  (prelude), ingest.py (tarball → pruned tree), agent.py.

---

## Implementation plan (v1)

Decision-complete; deviations need a reason recorded here.

### Server

**DB (alembic migration):** new table `paper_repos` — `id`, `paper_id`
(FK, UNIQUE — one repo per paper), `owner`, `repo`, `ref`,
`commit_sha`, `status` (`pending|ingesting|ready|error`), `error`
(text), `file_count`, `total_bytes`, `s3_prefix`, timestamps. Papers
table untouched.

**Ingestion** (`app/llm/repo/ingest.py`, adapted from spike):
1. Parse + validate GitHub URL (strict regex on owner/repo; reject
   anything else — SSRF surface is exactly one host).
2. Resolve default branch + head SHA via GitHub API (unauthenticated).
   Resolution failure = ingestion `error` — NEVER guess or infer a SHA
   from the archive; permalinks must match the stored bytes.
3. Download `codeload.github.com/{owner}/{repo}/tar.gz/{sha}` (by the
   RESOLVED SHA, not the branch), hard compressed-size cap (200 MB),
   redirects disabled except a single hop to
   `objects.githubusercontent.com`, connect/read timeouts, reject any
   `..` in owner/repo/ref path components.
4. Prune DURING extraction (member-by-member, never `extractall`):
   regular files only (reject symlinks/hardlinks/specials), tar-slip
   path sanitization, skip `.git`/`__pycache__`/`node_modules`,
   binaries (extension blocklist + NUL sniff + UTF-8 decode check),
   files > 1 MB, and paths containing control chars or `|`/`]`
   (citation-delimiter safety). Caps: 5,000 kept files, 100 MB kept
   bytes, 50,000 scanned members — abort over cap. Skips recorded in
   the manifest. No unpruned tree ever touches disk.
5. Write the pruned tree + `manifest.json` (paths, sizes,
   owner/repo/ref/sha, skip summary) directly to
   `{REPO_STORAGE_DIR}/{paper_id}/{sha}/` — a named docker volume
   mounted into the server container (compose change; dev servers use
   a local dir via env var). Extract into `{sha}.tmp.{pid}`, atomic
   rename into place, `.done` marker gates readers. **NO S3** — on a
   single-host deploy the volume IS the durability; if it's ever
   lost, re-connecting the repo re-ingests idempotently.
   (Cross-review revision: replaces the earlier S3 + local-cache
   design — deletes the materialize path, cross-process cache locks,
   the LRU sweeper, and a second deletion path.)
6. Runs via FastAPI `BackgroundTasks` with a FRESH `SessionLocal`
   (never the request session); status transitions CAS-style on the
   row; failures land in `status=error` + `error`. NO Celery/jobs.
   `POST` re-kicks (resets to `pending`) when the row is `error` or
   `ingesting` with `updated_at` older than 15 min (container restart
   mid-ingest must not strand the row); 409 only for fresh
   `ingesting`/`ready`. DELETE while freshly `ingesting` → 409.

**Snapshot access**: mount, prelude, viewer endpoint, and citation
verification all read only `{REPO_STORAGE_DIR}/{paper_id}/{sha}/`.
No LRU / eviction in v1 — growth is bounded by connected repos
(≤100 MB each); paper deletion and repo DELETE `rmtree` the dir (FK
`ON DELETE CASCADE` on the row).

**Sandbox** (`app/llm/repo/sandbox.py`, adapted from spike): lazy
module-level `Monty(request_timeout=25.0)` pool; per-agent-run session
(`max_duration_secs=240`, memory limit sized generously), `MountDir`
read-only at `/repo` passed on EVERY feed; an asyncio/thread lock
serializes feeds within a run (pydantic-ai may parallelize tool
calls); poison detection → transparent session rebuild + explicit
"session RESET, redefine your variables" notice appended to output.
Session is per-turn only — no cross-turn state.

**Prelude** (`app/llm/repo/prelude.py`, adapted from spike helpers):
host-side `tree(path, max_depth)`, `read(path, start, end)`
(line-numbered, ≤400 lines/call), `grep(pattern, path, glob, context,
max_results)` injected via `external_lookup`; all confined to the
cache root (resolve + prefix check); each call records touched repo
paths.

**Agent tool** (in `app/llm/chat/paper.py`): one new tool
`run_python(code: str)`, registered only when the paper's repo row is
`ready`. Returns `{"output": <str, truncated at 6000 chars>,
"files": <unique touched paths, ≤20>}`. Shares the existing
25-tool-call budget. System prompt gains a repo section only when the
tool is registered: accurate subset list (from §Trial results — this
is the model's ONLY source of truth), prelude signatures + one-line
examples, `/repo` mount, session-persistence + reset semantics, the
code-citation format, and a preloaded 2-level tree summary (~2 KB,
from the manifest) so the model skips the listdir warm-up.

**Code citations** (`app/llm/chat/citations.py`): parser learns
`file=` / `lines=A-B` extras. Reconciliation for code citations is
fully host-side (no LLM): read the file from the cache, verify the
quoted snippet appears at the claimed lines; if the quote exists
elsewhere, repair the line numbers; if absent, drop `lines` and mark
`verified: false`. Attach `github_url`
(`blob/{sha}/{path}#L{a}-L{b}`) server-side. PDF citation path
untouched.

**API** (`app/api/repo_api.py`, auth = paper owner, existing
patterns):
- `POST  /api/paper/{id}/repo` {url} → create row, kick ingestion (409
  if exists)
- `GET   /api/paper/{id}/repo` → status row
- `DELETE /api/paper/{id}/repo` → row + S3 objects
- `GET   /api/paper/{id}/repo/tree` → manifest
- `GET   /api/paper/{id}/repo/file?path=` → `{path, content, size,
  github_url}`; path must resolve inside the snapshot AND appear in
  the manifest (traversal defense), 404 otherwise

### Client

- `lib/shiki.ts` — lazy singleton highlighter (python, ts/js, json,
  yaml, toml, md, shell, c/cpp, rust, go; plain fallback), dual
  light/dark themes.
- `CodeBlock` — plugged into the markdown `components` maps used by
  chat (AnimatedMarkdown consumers + PaperChatPanel's react-markdown);
  streaming-aware (plain `<pre>` first, highlight once stable).
- `CodeViewerDialog` — two panes: file tree from manifest (collapsible
  dirs, sizes, filter input) + highlighted line-numbered file view
  with scroll-to/highlight range, GitHub permalink, copy. Degrade past
  a byte threshold to plain text.
- Repo connect UI on the paper page: paste URL → POST, poll GET while
  `ingesting`, show `ready` (file count) / `error`, disconnect.
- Tool-activity chips: `files` from `run_python` results render as
  clickable chips → CodeViewerDialog at that file.
- Citation rendering: code citations (have `file`) render with the
  GitHub icon/link and open the viewer at the cited lines; `verified:
  false` shown subtly (no line anchor).

### Deviations (as built)

Server: DB column is `storage_prefix` (S3 was cut, old name misled);
repo content lives under `tree/` inside the snapshot dir (repo files
must not collide with `manifest.json`/`.done`); unverified code
citations carry NO `github_url` at all; `pending` counts as in-flight
for POST/DELETE 409s (staleness rule extended); citation verifier
strips `NN|` line-number prefixes models copy from `read()` output
(first number doubles as start-line hint); prelude CPU budgets are
per-FEED shared across helpers with a 200-call cap (per-call budgets
let a sandbox loop burn GIL-held host CPU). Client: repo-connect UI
is a popover in the chat panel header (not a paper-page section);
disconnect confirm is inline (Radix dialog-in-popover focus fight);
`AnimatedMarkdown`'s streaming splitter is now fence-aware (blank
lines inside fenced code no longer bisect blocks — affects all
markdown surfaces, deliberate). Dev: uvicorn needs
`--reload-exclude '.repo_snapshots/*'` or ingestion restarts the dev
server.

### Explicitly cut from v1

Multi-repo per paper; branch/ref picker; repo auto-detection from
paper text; private repos; docs querying; cross-turn sandbox state;
Celery ingestion; PDF-side changes of any kind.

### Cross-review revisions (binding — codex + Opus pass, reconciled)

1. **Pool sizing**: `Monty(request_timeout=25.0, min_processes=0,
   max_processes=2, checkout_timeout=10.0)` per gunicorn worker
   (workers = cpu*2+1 — defaults would multiply out to hundreds of
   sandbox processes), plus a module-level semaphore(2); checkout
   timeout → friendly "sandbox busy, retry" tool result, never a hang.
2. **Lifecycle**: session + `MountDir` are created by `run_paper_chat`
   (not inside the tool) and closed in its existing `finally` BEFORE
   the `aclose()` awaits — those can re-raise `CancelledError` and
   skip anything after them. A user hitting stop must not leak a pool
   worker or mount fd.
3. **Prelude DoS**: `grep` uses the `regex` package with a per-search
   timeout (~50 ms/line-batch), cumulative scanned-bytes budget
   (~20 MB) and wall-clock deadline checked between files → "search
   too broad, narrow with path=/glob=" message on trip. Host
   callbacks run IN the API process holding the GIL —
   `request_timeout` does not guard them. Clamp all prelude args
   (`context ≤ 10`, `max_results ≤ 200`, pattern length ≤ 500).
   Path confinement via `Path.resolve()` + `is_relative_to()` +
   manifest membership — never string `startswith`.
4. **Tool return vs wire cap**: `truncate_tool_output` replaces the
   WHOLE structure with a preview blob past 6,000 serialized chars.
   Cap `output` at 4,000 chars inside the tool and put `files` first
   in the dict so chips survive.
5. **History bloat**: add `strip_sandbox_outputs` beside
   `strip_figure_bytes` — cap `run_python` ToolReturnPart content at
   ~1,500 chars before persisting to `bucket.pai_messages` (16-call
   turns would otherwise blow the 240k replay budget in ~2 turns,
   degrading the whole conversation).
6. **Dispatch**: `run_python` gets its own small executor +
   `asyncio` lock — NOT the shared `_tool_executor(4)` (25s feeds
   would starve every other tool call in the process) and no
   `SessionLocal` (it doesn't need one). Own telemetry event.
7. **Citations**: reconciler branches on `cit.get("file")` BEFORE the
   `page is None` short-circuit (which would otherwise swallow code
   citations verbatim). Verification is whitespace-normalized
   matching mapped back to line numbers (the parser flattens quote
   whitespace); `github_url` (percent-encoded per segment) attached
   only after verification; unverifiable → drop `lines`, mark
   `verified: false`. Client PDF-highlight path must skip citations
   carrying `file`.
8. **Budgets**: `UsageLimits(request_limit=MAX_AGENTIC_ITERATIONS+5)`
   (was +2 — one over-budget tool call would raise instead of
   degrade); `run_python` sub-budget of 12 inside the shared 25 so
   repo exploration can't consume the paper tools' allowance.
9. **Tree-in-prompt**: keep the preloaded tree summary (consistent
   with existing posture — paper content is already untrusted prompt
   input) but sanitize: strip control chars, cap line length and
   total size, wrap in explicit untrusted-content delimiters.
10. **Deps**: pin `pydantic-monty==0.0.21`; verify the wheel imports
    and runs a mount round-trip on the server image's
    Linux/py-version (trials were macOS/py3.14) EARLY — it gates
    everything.

### Testing

Unit: ingest filtering (binaries/oversize/tar-slip), URL validation,
citation verify/repair (exact/moved/absent), prelude path confinement,
sandbox poison-reset, file-endpoint traversal. Integration: one live
DeepSeek smoke against nanoGPT through the real chat endpoint
(spike's ingested tree can seed it). `next build` + server tests
green.
