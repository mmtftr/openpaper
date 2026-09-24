"""Host-side helpers injected into the sandbox via `external_lookup`.

Also called directly, with no sandbox in between, by the quick question's
read-only tools (`app.llm.chat.quick_question_tools`).

Monty's `os` has no `walk` and its `pathlib.Path` has no `glob`/`rglob`, so
recursive navigation would otherwise be hand-rolled by the model on every
call. These three functions run on the host at native speed and return
plain strings the model can slice with its own Python in the same snippet.

SECURITY / DoS NOTE: these callbacks execute IN the API process, holding the
GIL. Monty's `request_timeout` does NOT guard them — a pathological regex or
an unbounded scan would stall a gunicorn worker. Hence: the `regex` package
with a per-line timeout, a cumulative scanned-bytes budget, a wall-clock
deadline checked between files, and hard clamps on every argument.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import regex

from app.llm.repo.storage import SnapshotPathError, resolve_within

logger = logging.getLogger(__name__)

VIRTUAL_ROOT = "/repo"

MAX_READ_LINES = 400
MAX_TREE_ENTRIES = 600
MAX_TREE_DEPTH = 8
MAX_CONTEXT = 10
MAX_RESULTS = 200
MAX_PATTERN_LEN = 500

# DoS budgets. These are PER FEED, not per helper call: one `run_python`
# snippet can call the helpers in a loop, and per-call budgets would simply
# multiply (measured: 400 greps in one snippet = 85 s of GIL-holding work
# inside the API process, which `request_timeout` cannot interrupt because
# the sandbox worker is idle the whole time).
GREP_SCAN_BYTE_BUDGET = 20 * 1024 * 1024
GREP_WALL_DEADLINE = 6.0
GREP_LINE_TIMEOUT = 0.05
MAX_HELPER_CALLS_PER_FEED = 200
# Output-side caps for grep, applied WHILE the result is built. Each emitted
# line is clamped (a minified bundle would otherwise put a multi-megabyte
# line into every hit and its context) and the result stops growing past
# GREP_MAX_OUTPUT_CHARS. Sized above the model-facing caps (the sandbox's
# MAX_TOOL_OUTPUT, the quick question's MAX_LOOKUP_OUTPUT) so those still
# decide what the model sees; this one only bounds the transient allocation
# — up to (2*context+1) x scan budget before — made while holding the GIL.
GREP_MAX_LINE_CHARS = 500
GREP_MAX_OUTPUT_CHARS = 40_000
# The deadline is checked before every match, not every N lines: a regex may
# legitimately burn up to GREP_LINE_TIMEOUT per line, so checking every 256
# lines would overshoot by ~12 s.
_DEADLINE_CHECK_EVERY = 1

_TOO_BROAD = (
    "grep: search budget exhausted after scanning {scanned:,} bytes in "
    "{files} files — the search was too broad. Narrow it with path= "
    "(a subdirectory) or glob= (e.g. '*.py'), or use a more specific pattern."
)

_BUDGET_SPENT = (
    "[budget] This run_python call has used up its file-scanning budget "
    "(time or bytes). Nothing more can be read or searched in THIS snippet — "
    "print what you already have and continue in the next run_python call "
    "with a narrower path=/glob=."
)


class RepoPrelude:
    """Bound to one published snapshot; produces the `external_lookup` map
    for the sandbox, or serves the quick-question tools directly.

    `manifest_paths` is the authoritative file list. Every path a helper
    touches must resolve inside the snapshot root AND appear in the
    manifest, so a stale or hand-planted file in the directory is invisible.
    """

    def __init__(
        self,
        root: Path,
        manifest_files: Sequence[Dict[str, Any]],
        *,
        virtual_root: str = VIRTUAL_ROOT,
    ):
        self.root = Path(root).resolve()
        self.virtual_root = virtual_root.rstrip("/") or VIRTUAL_ROOT
        self._sizes: Dict[str, int] = {}
        for entry in manifest_files:
            if isinstance(entry, dict):
                path, size = entry.get("path"), entry.get("size", 0)
            else:  # tolerate a bare path list
                path, size = entry, 0
            if isinstance(path, str) and path:
                try:
                    self._sizes[path] = int(size or 0)
                except (TypeError, ValueError):
                    self._sizes[path] = 0
        self.paths: frozenset = frozenset(self._sizes)
        self._dirs: frozenset = frozenset(
            str(parent) for path in self.paths for parent in Path(path).parents
        )
        # Ordered, de-duplicated repo paths touched by the CURRENT
        # run_python call (for the UI's file chips on that tool row).
        self._touched: Dict[str, None] = {}
        # Per-feed DoS budgets, shared by every helper (see module docstring).
        self._deadline: float = 0.0
        self._scanned_bytes: int = 0
        self._helper_calls: int = 0
        self.start_call()

    # ---- touched-file log + per-feed budget --------------------------------

    def start_call(self) -> None:
        """Begin a new run_python feed (or one quick-question lookup).

        Resets the chips (they are per tool row) AND the DoS budgets — one
        snippet gets one wall deadline, one scan allowance and one helper-call
        allowance no matter how many helper calls it makes.
        """
        self._touched = {}
        self._deadline = time.monotonic() + GREP_WALL_DEADLINE
        self._scanned_bytes = 0
        self._helper_calls = 0

    @property
    def _budget_left(self) -> bool:
        return (
            time.monotonic() < self._deadline
            and self._scanned_bytes <= GREP_SCAN_BYTE_BUDGET
            and self._helper_calls <= MAX_HELPER_CALLS_PER_FEED
        )

    def _charge_call(self) -> Optional[str]:
        """Account one helper call; return the trip message when spent."""
        self._helper_calls += 1
        if self._helper_calls > MAX_HELPER_CALLS_PER_FEED:
            return (
                f"[budget] This snippet already made {MAX_HELPER_CALLS_PER_FEED} "
                "helper calls. Stop looping over tree()/read()/grep() — make a "
                "few targeted calls, print what you need, and continue in the "
                "next run_python call."
            )
        if not self._budget_left:
            return _BUDGET_SPENT
        return None

    @property
    def touched(self) -> List[str]:
        return list(self._touched.keys())

    def _record(self, rel: str) -> None:
        if rel and rel in self.paths:
            self._touched[rel] = None

    # ---- path translation -------------------------------------------------

    def _relative(self, virtual: str) -> str:
        """Sandbox path (`/repo/a/b.py` or `a/b.py`) → repo-relative path."""
        text = str(virtual or "").strip()
        if not text:
            return ""
        # Component-aware prefix check: a bare `startswith` would mangle
        # `/repository/x` into `sitory/x` and `/repo-evil/x` into `-evil/x`.
        if text == self.virtual_root:
            return ""
        if text.startswith(self.virtual_root + "/"):
            text = text[len(self.virtual_root) + 1 :]
        elif text.startswith("/"):
            raise ValueError(
                f"path must be under {self.virtual_root}/ — got {virtual!r}"
            )
        return text.strip("/")

    def _resolve(self, virtual: str) -> tuple[str, Path]:
        rel = self._relative(virtual)
        if not rel:
            return "", self.root
        return rel, resolve_within(self.root, rel)

    def _is_known_dir(self, rel: str) -> bool:
        return rel == "" or rel in self._dirs

    # ---- helpers ----------------------------------------------------------

    def tree(self, path: str = VIRTUAL_ROOT, max_depth: int = 3) -> str:
        """Directory tree rendered from the manifest (not the filesystem).

        Files show their byte size; directories deeper than `max_depth` are
        collapsed to `name/ (N files)` so the model knows where to recurse.
        """
        if spent := self._charge_call():
            return spent
        try:
            rel, _ = self._resolve(path)
        except (ValueError, SnapshotPathError) as exc:
            return f"tree: {exc}"
        depth = max(1, min(int(max_depth or 1), MAX_TREE_DEPTH))

        prefix = f"{rel}/" if rel else ""
        if rel and rel in self.paths:
            return f"{path} is a file, not a directory — use read({path!r})"
        if not self._is_known_dir(rel):
            return f"tree: no such directory: {path}"

        sizes = self._sizes
        entries = sorted(
            entry for entry in self.paths if not prefix or entry.startswith(prefix)
        )
        if not entries:
            return f"tree: {path} is empty"

        lines = [f"{self.virtual_root}/{prefix}".rstrip("/") + "/"]
        collapsed: Dict[str, int] = {}
        rendered_dirs: set = set()
        truncated = False

        for entry in entries:
            parts = entry[len(prefix) :].split("/")
            if len(parts) > depth:
                # Collapse everything below the depth limit into a counter on
                # the deepest directory we are still allowed to show.
                node = "/".join(parts[:depth])
                collapsed[node] = collapsed.get(node, 0) + 1
                continue
            if len(lines) > MAX_TREE_ENTRIES:
                truncated = True
                break
            for level in range(len(parts) - 1):
                directory = "/".join(parts[: level + 1])
                if directory in rendered_dirs:
                    continue
                rendered_dirs.add(directory)
                lines.append(f"{'    ' * level}{parts[level]}/")
            size = sizes.get(entry, 0)
            lines.append(f"{'    ' * (len(parts) - 1)}{parts[-1]} ({size})")

        for node in sorted(collapsed):
            parts = node.split("/")
            for level in range(len(parts) - 1):
                directory = "/".join(parts[: level + 1])
                if directory in rendered_dirs:
                    continue
                rendered_dirs.add(directory)
                lines.append(f"{'    ' * level}{parts[level]}/")
            if len(lines) > MAX_TREE_ENTRIES:
                truncated = True
                break
            lines.append(
                f"{'    ' * (len(parts) - 1)}{parts[-1]}/ "
                f"({collapsed[node]} files — raise max_depth or call tree() here)"
            )

        if truncated:
            lines.append(
                f"... [tree truncated at {MAX_TREE_ENTRIES} entries; call "
                "tree() on a subdirectory]"
            )
        return "\n".join(lines)

    def read(self, path: str, start: int = 1, end: Optional[int] = None) -> str:
        """File contents with line numbers, capped at 400 lines per call."""
        if spent := self._charge_call():
            return spent
        try:
            rel, host = self._resolve(path)
        except (ValueError, SnapshotPathError) as exc:
            return f"read: {exc}"
        if not rel or rel not in self.paths:
            return f"read: no such file: {path}"
        # Charge against the feed's scan budget BEFORE reading, from the
        # manifest size (bytes on disk, not decoded characters).
        self._scanned_bytes += self._sizes.get(rel, 0)
        if not self._budget_left:
            return _BUDGET_SPENT
        try:
            text = host.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"read: cannot read {path}: {exc}"
        self._record(rel)

        lines = text.split("\n")
        total = len(lines)
        try:
            start_line = max(1, int(start))
        except (TypeError, ValueError):
            start_line = 1
        try:
            end_line = total if end is None else min(int(end), total)
        except (TypeError, ValueError):
            end_line = total
        if end_line < start_line:
            end_line = start_line
        if end_line - start_line + 1 > MAX_READ_LINES:
            end_line = start_line + MAX_READ_LINES - 1
        end_line = min(end_line, total)

        chunk = lines[start_line - 1 : end_line]
        width = len(str(end_line))
        body = "\n".join(
            f"{start_line + i:>{width}}| {line}" for i, line in enumerate(chunk)
        )
        header = f"{self.virtual_root}/{rel} (lines {start_line}-{end_line} of {total})"
        footer = ""
        if end_line < total:
            footer = (
                f"\n... [{total - end_line} more lines; call "
                f"read({path!r}, start={end_line + 1})]"
            )
        return f"{header}\n{body}{footer}"

    def grep(
        self,
        pattern: str,
        path: str = VIRTUAL_ROOT,
        glob: str = "*",
        context: int = 2,
        max_results: int = 50,
    ) -> str:
        """Regex search. Output is `file:line: text` with context lines."""
        if spent := self._charge_call():
            return spent
        pattern_text = str(pattern or "")
        if not pattern_text:
            return "grep: pattern is required"
        if len(pattern_text) > MAX_PATTERN_LEN:
            return f"grep: pattern is too long (max {MAX_PATTERN_LEN} chars)"
        try:
            compiled = regex.compile(pattern_text)
        except regex.error as exc:
            return f"grep: bad regex {pattern_text!r}: {exc}"

        try:
            rel, _ = self._resolve(path)
        except (ValueError, SnapshotPathError) as exc:
            return f"grep: {exc}"

        context_lines = max(0, min(int(context or 0), MAX_CONTEXT))
        result_cap = max(1, min(int(max_results or 1), MAX_RESULTS))

        suffix, glob_error = _glob_suffix(glob)
        if glob_error:
            # Silently ignoring an unsupported glob turns the model's attempt
            # to NARROW the search into a whole-repo scan.
            return glob_error
        prefix = f"{rel}/" if rel else ""
        if rel and rel in self.paths:
            targets = [rel]
        else:
            if not self._is_known_dir(rel):
                return f"grep: no such path: {path}"
            targets = sorted(
                entry
                for entry in self.paths
                if (not prefix or entry.startswith(prefix))
                and (suffix is None or entry.endswith(suffix))
            )
        if not targets:
            return f"grep: no files under {path}" + (
                f" matching glob={glob}" if suffix else ""
            )

        out: List[str] = []
        out_chars = 0
        hits = 0
        files_with_hits = 0

        for rel_path in targets:
            # Charge from the manifest size before opening: decoded character
            # count would undercount UTF-8 input by up to 4x.
            self._scanned_bytes += self._sizes.get(rel_path, 0)
            if not self._budget_left:
                return _budget_message(out, self._scanned_bytes, files_with_hits, hits)
            try:
                host = resolve_within(self.root, rel_path)
                content = host.read_text(encoding="utf-8", errors="replace")
            except (OSError, SnapshotPathError):
                continue
            lines = content.split("\n")

            matched: List[int] = []
            for index, line in enumerate(lines):
                # Before EVERY match: a single line may legitimately consume
                # the full per-line timeout, so a coarser check overshoots the
                # wall deadline by seconds.
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    return _budget_message(
                        out, self._scanned_bytes, files_with_hits, hits
                    )
                try:
                    if compiled.search(
                        line, timeout=min(GREP_LINE_TIMEOUT, remaining)
                    ):
                        matched.append(index)
                except TimeoutError:
                    if self._deadline - time.monotonic() <= 0:
                        return _budget_message(
                            out, self._scanned_bytes, files_with_hits, hits
                        )
                    return (
                        f"grep: the pattern {pattern_text!r} is too expensive to "
                        "evaluate (catastrophic backtracking). Simplify it — "
                        "avoid nested quantifiers like (a+)+."
                    )
                if len(matched) >= result_cap:
                    break
            if not matched:
                continue

            files_with_hits += 1
            self._record(rel_path)
            for index in matched:
                if hits >= result_cap:
                    out.append(
                        f"... [result cap {result_cap} reached; {files_with_hits} "
                        "files matched so far — narrow with path= or glob=, or "
                        "raise max_results]"
                    )
                    return "\n".join(out)
                hits += 1
                low = max(0, index - context_lines)
                high = min(len(lines), index + context_lines + 1)
                for j in range(low, high):
                    marker = ":" if j == index else "-"
                    row = (
                        f"{self.virtual_root}/{rel_path}:{j + 1}{marker} "
                        f"{_clamp_grep_line(lines[j])}"
                    )
                    out.append(row)
                    out_chars += len(row) + 1
                out.append("--")
                if out_chars >= GREP_MAX_OUTPUT_CHARS:
                    out.append(
                        f"... [output cap reached after {hits} matches in "
                        f"{files_with_hits} files — narrow with path= or glob=, "
                        "or use a more specific pattern]"
                    )
                    return "\n".join(out)

        if not out:
            return (
                f"grep: no matches for {pattern_text!r} under {path}"
                + (f" (glob={glob})" if suffix else "")
            )
        return "\n".join(out) + f"\n[{hits} matches in {files_with_hits} files]"

    def as_external_lookup(self) -> Dict[str, Callable]:
        return {"tree": self.tree, "read": self.read, "grep": self.grep}


def _clamp_grep_line(text: str) -> str:
    if len(text) <= GREP_MAX_LINE_CHARS:
        return text
    return text[:GREP_MAX_LINE_CHARS] + " …[line truncated]"


def _budget_message(
    out: List[str], scanned: int, files: int, hits: int
) -> str:
    trip = _TOO_BROAD.format(scanned=scanned, files=files)
    if out:
        return "\n".join(out) + f"\n[{hits} matches so far]\n{trip}"
    return trip


def _glob_suffix(glob: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(suffix filter, error message) for the `*.ext` / `.ext` forms.

    Anything else is an ERROR rather than a silent no-filter: a model writing
    `glob='**/*.py'` to narrow a search would otherwise trigger the whole-repo
    scan it was trying to avoid.
    """
    text = str(glob or "").strip()
    if not text or text == "*":
        return None, None
    if text.startswith("*.") and "/" not in text and "*" not in text[2:]:
        return text[1:], None
    if text.startswith(".") and "/" not in text and "*" not in text:
        return text, None
    if "." in text and "*" not in text and "/" not in text:
        return text, None
    return None, (
        f"grep: unsupported glob {text!r}. Only a simple extension filter is "
        "supported (e.g. glob='*.py'). To restrict the search to a "
        "subdirectory use path='/repo/subdir' instead."
    )


PRELUDE_DOC = """\
Three host-provided helper functions already exist as plain globals. Do NOT
import them, and do not redefine them:

  tree(path='/repo', max_depth=3) -> str
      Directory listing. e.g. tree('/repo')   tree('/repo/pipeline', max_depth=2)

  read(path, start=1, end=None) -> str
      File contents with line numbers, max 400 lines per call.
      e.g. read('/repo/train.py')   read('/repo/model.py', 100, 180)

  grep(pattern, path='/repo', glob='*', context=2, max_results=50) -> str
      Regex search; output is `file:line: text` with context lines.
      e.g. grep('def get_lr')   grep('dropout', glob='*.py')

They return strings — print() them, or slice/split them with your own Python
in the same snippet. Keep searches narrow (pass path= or glob=): a search
that scans the whole repo can hit the search budget and return nothing.
"""
