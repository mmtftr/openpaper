# Monty + DeepSeek code-inspection spike

Feasibility spike for giving openpaper's paper-chat agent a `run_python(code)`
tool that inspects a paper's companion GitHub repo inside a Monty sandbox.

**Question:** can `DeepSeek-V4-Flash-0731` — our cheapest/weakest served chat
model — drive it? If it can, everything can.

Everything here lives in `spikes/monty-deepseek/`. Nothing under `server/`,
`client/`, or `jobs/` was touched, and nothing was committed.

- Monty version probed: `pydantic-monty` **0.0.21** (macOS arm64, CPython 3.14)
- pydantic-ai: **1.89.1** (matches prod pin)
- Models: `DeepSeek-V4-Flash-0731`, `gpt-5.4-mini` — Azure resource
  `your-azure-resource`, v1 endpoint, **Responses** API

---

## 1. Monty's Python API — what's actually there

### 1.1 Directory mounts are supported (no fallback needed)

The Python package **does** support host directory mounts, so the
`external_lookup`-backed in-memory dict fallback was not needed as the
primary mechanism:

```python
from pydantic_monty import Monty, MountDir

with Monty(request_timeout=25.0) as pool:
    with MountDir(host_path=repo_dir, virtual_path='/repo', mode='read-only') as mount:
        with pool.checkout(limits={'max_duration_secs': 240.0}) as session:
            session.feed_run("open('/repo/train.py').read()", mount=mount)
```

`MountDir` takes keyword-only args: `host_path`, `virtual_path`, `mode`
(`'read-only'` / `'read-write'` / `'overlay'`, default overlay),
`write_bytes_limit`, `memory_usage_limit` (default 100 MB).

**Gotcha: a mount is per-feed, not per-session.** It must be passed to *every*
`feed_run` call. Omitting it on a later call doesn't fall back to "no files" —
it raises `PermissionError: Permission denied: '/repo/notes.txt'`, which reads
like a security failure rather than a wiring bug. Verified in
`probe3_limits.py` (Q6).

Read-only mode holds up: writes raise
`PermissionError: [Errno 30] Read-only file system: '/repo/x.txt'`, and reads
outside the mount raise `PermissionError: Permission denied: '/etc/passwd'`.

`external_lookup` also works and is genuinely useful (see the PRELUDE arm in
§4). It injects host callables resolved lazily by name, per feed. It happily
returns **large** strings across the boundary — a 1.48 MB / 26,795-line grep
result came back with no error and no measurable overhead.

### 1.2 The interpreter is a much smaller subset than expected

This is the headline finding, and it invalidates the module list in the
original spike brief. Measured by *calling* each candidate
(`probe5_calls.py`) — note that `getattr()` cannot be used to introspect,
because Monty raises `TypeError: getattr(): attribute is not a simple value`
for methods, which produces false negatives.

**Modules that exist (12):** `json`, `re`, `pathlib`, `os`, `math`,
`itertools`, `collections`, `dataclasses`, `datetime`, `typing`, `sys`,
`unicodedata`.

**Modules that do NOT exist** — including several the brief assumed:
`functools`, `os.path`, `io`, `string`, `textwrap`, `ast`, `csv`, `base64`,
`hashlib`, `time`, `random`, `statistics`, `enum`, `heapq`, `bisect`, `copy`,
`operator`, `fnmatch`, `glob`, `difflib`, `subprocess`, `socket`, `shutil`,
`urllib`, `decimal`, `uuid`, `logging`, `pprint`. And no third-party packages
(`import numpy` → `ModuleNotFoundError: No module named 'numpy'`).

**Navigation gaps that matter most for this feature:**

| Missing | Error | Consequence |
|---|---|---|
| `os.walk` | `AttributeError: module 'os' has no attribute 'walk'` | recursive walk must be hand-rolled |
| `os.path` (whole submodule) | `AttributeError: 'module' object has no attribute 'path'` | no `join`/`isdir`/`getsize` |
| `Path.glob` / `Path.rglob` | `AttributeError: 'PosixPath' object has no attribute 'glob'` | no glob-based file discovery |
| `Path.relative_to` | `AttributeError` | path math is manual |
| `os.scandir`, `os.getcwd` | `AttributeError` | — |
| `os.environ`, `os.getenv` | `RuntimeError: 'os.environ' is not supported in this environment` | (deliberate; good) |

Other absences that trip up generated code: `json.load` (only `loads`),
`re.subn`, `re.VERBOSE`, `itertools.groupby`/`product`,
`collections.OrderedDict`, and **`str.format`** (f-strings work fine).

**Language features not supported by the parser:**

- class **inheritance** and metaclasses — `NotImplementedError: The monty
  syntax parser does not yet support class inheritance and metaclasses`
- `yield` / generator functions — `NotImplementedError: ... does not yet
  support yield expressions`
- `match` statements — `NotImplementedError: ... does not yet support pattern
  matching (match statements)`
- builtins absent: `dir`, `vars`, `eval`, `exec`, `compile`, `globals`,
  `locals`, `input`, `__import__`, `bytearray`, `format`, `callable`,
  `issubclass`, `super`, `staticmethod`, `classmethod`

**What works well** — and it is enough to read code with: `open().read()`,
`Path.read_text/iterdir/is_dir/is_file/exists/stat/parts/name/suffix/parent`,
the `/` path operator, `os.listdir`, `os.stat`, the whole practical `re`
surface (`search`/`finditer`/`findall`/`sub`/`compile`/`escape`/`M`/`I`/`S`,
groups and spans), all common string methods, comprehensions, `sorted(key=)`,
lambdas, closures, default/`*args`/`**kwargs`, f-strings, dataclasses,
`try/except/finally`, walrus, star-unpacking, `global`, plain (non-inheriting)
classes.

A hand-rolled recursive walk over the 2,269-file pydantic-ai tree using only
`os.listdir` + `Path(...).is_dir()` runs in **0.12 s**, and a regex grep over
its 649 Python files in **0.34 s**. Performance is a non-issue.

### 1.3 Resource limits: the operational trap

`ResourceLimits` accepts `max_duration_secs`, `max_memory`, `gc_interval`,
`max_recursion_depth`. The semantics are not what the names suggest, and
getting this wrong would silently break the feature in production.

**`max_duration_secs` is a CUMULATIVE execution budget for the whole session,
not a per-call timeout.** With a 5 s budget, four consecutive 1.2 s snippets
succeeded and the fifth failed instantly (`probe3_limits.py` Q1). An 8 s
host-side pause between feeds did *not* consume budget (Q1b), confirming it
counts sandbox execution time, not wall clock.

**Once the budget is exhausted the session is permanently poisoned.** Every
subsequent feed — including `1 + 1` — raises
`TimeoutError: time limit exceeded: 2.000020709s > 2s` in ~0 ms, forever.
The same is true after a `MemoryError`. In the very first probe run this
cascaded: one runaway `while True` loop poisoned the session and the next
13 unrelated probes all reported bogus timeouts.

Mitigations, both implemented in `sandbox.py`:

1. **Two separate guards.** The pool's parent-side `request_timeout` *is* a
   genuine per-call deadline (25 s here); exceeding it kills the worker and
   raises `MontyCrashedError(timed_out=True)`. `max_duration_secs` (240 s) is
   used only as a whole-conversation cap.
2. **Detect and rebuild.** Poisoning is trivially detectable (the same limit
   error, returned in ~0 ms). On detection the wrapper transparently checks
   out a fresh session and appends an explicit notice to the tool output:
   *"This session exceeded its resource budget and has been RESET. All
   variables and function definitions from earlier run_python calls are gone
   — redefine anything you still need."* Verified end-to-end: after a runaway
   loop, `marker` correctly raises `NameError` and `1 + 1` returns `2`.

Memory does **not** leak across feeds — six consecutive 20 MB allocations
under a 128 MB cap all succeeded, so ordinary repeated file reading is safe.

### 1.4 Error-message quality — excellent

This matters more than any other factor, because the model iterates on these
strings. Monty produces real CPython-style tracebacks with source lines and
caret underlines:

```
Traceback (most recent call last):
  File "<python-input-45>", line 17, in <module>
    files = walk('/repo')
            ~~~~~~~~~~~~~
  File "<python-input-45>", line 11, in walk
    if os.path.isdir(full):
AttributeError: 'module' object has no attribute 'path'
```

`NotImplementedError` messages name the exact unsupported construct
("does not yet support yield expressions"), which is far more actionable than
a generic `SyntaxError`. `MontyRuntimeError.display()` takes
`'traceback' | 'type-msg' | 'msg'`, so the wrapper can pick verbosity;
`.display('type-msg')` is also what makes cheap poison-detection possible.

### 1.5 Surprises worth recording

- `getattr()` on a method raises `TypeError: getattr(): attribute is not a
  simple value`. Combined with the absence of `dir()`, the sandbox is
  **not introspectable from the inside** — the model cannot discover the API
  by poking at it, so the system prompt has to be accurate. This is the single
  strongest argument for shipping a carefully-maintained prompt.
- `pydantic-monty` 0.0.21 installs three distribution packages
  (`pydantic-monty`, `-client`, `-runtime`); the Rust binary comes from
  `-runtime` and is resolved via `MONTY_BIN`, the env's scripts dir, or `PATH`.
- `MontySession.install_dependencies(requirements)` exists in the API, but is
  irrelevant here (we want no third-party imports).
- Print capture via `CollectStreams` is fast and lossless: 10,000 printed
  lines (98,890 bytes) came back in 0.01 s.

---

## 2. `CodeModeToolset` in pydantic-ai 1.89.1 — NOT available

Checked both the spike venv and the pinned prod version (both 1.89.1):

- `pydantic_ai.toolsets.code_mode` → `ModuleNotFoundError`
- `pydantic_ai.toolsets.CodeModeToolset` → absent
- `pydantic_ai.capabilities.CodeMode` → absent (the `capabilities` package
  exports 40+ names; `CodeMode` is not among them)

Only vestigial references exist — comments in `tool_manager.py` and
`agent/__init__.py` mentioning "wrapper toolsets (e.g. `CodeModeToolset`)",
and a `SetToolMetadata(code_mode=True)` flag. **Using it would require a
pydantic-ai version bump.** The hand-rolled single-tool agent used in this
spike needs no bump and is ~30 lines, so this is not a blocker either way.

---

## 3. Repo ingestion

`ingest.py` downloads `https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}`
(no auth for public repos), extracts, then builds a *pruned copy* that becomes
the mount root. Filtering has to be physical because `MountDir` exposes a whole
host directory. Filters: skip `.git`/`__pycache__`/`node_modules`/etc., skip
oversized files, skip binaries (extension blocklist + NUL-byte sniff + UTF-8
decode check).

### The 200 KB cap was a real bug — raise it to ~1 MB

The brief's suggested "skip files > 200 KB" quietly destroyed two of the six
trial questions. In pydantic-ai it dropped:

- `pydantic_ai_slim/pydantic_ai/agent/__init__.py` — **212 KB**, the concrete
  `Agent` class
- `pydantic_ai_slim/pydantic_ai/models/openai.py` — **282 KB**

138 text files exceeded 200 KB. Asking "where is retry logic implemented?" of a
tree whose central module is missing is unanswerable, and worse, it invites the
model to *confabulate* from training-data recall of a file it cannot see.
Real source files get big; the cap was raised to **1 MB** and the repo
re-ingested before the trials were run.

| Repo | Tarball | Files kept | Bytes | Skipped |
|---|---|---|---|---|
| `karpathy/nanoGPT@master` | 435 KB | 23 | 82 KB | 2 binary, 1 oversized |
| `pydantic/pydantic-ai@main` (200 KB cap) | 155 MB | 2,269 | 42.3 MB | 23 binary, **137 oversized** |
| `pydantic/pydantic-ai@main` (1 MB cap, used) | 155 MB | **2,355** | **81.3 MB** | 27 binary, 47 oversized |

At 81 MB of retained text the mount's `memory_usage_limit` (default 100 MB,
covering transient filesystem results) becomes a live risk, so the spike raises
it to 512 MB. Anyone building this should size that against the ingested tree,
not leave it at the default.

