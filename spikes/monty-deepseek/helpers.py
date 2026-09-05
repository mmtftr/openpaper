"""Host-side helper functions exposed inside the Monty sandbox.

These run on the host at native speed (real `os.walk`, real `re`) and are
injected into each `feed_run` via `external_lookup`, so sandbox code can call
`tree(...)`, `read(...)`, `grep(...)` and freely compose the results with its
own Python in the same snippet.

Why they exist: Monty's `os` has no `walk`, and its `pathlib.Path` has no
`glob`/`rglob`, so recursive navigation must otherwise be hand-rolled with
`os.listdir` + `Path(...).is_dir()` -- a real burden on a weak model.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

VIRTUAL_ROOT = "/repo"

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
             ".ruff_cache", ".pytest_cache"}


class RepoHelpers:
    """Bound to one ingested repo tree; produces the external_lookup mapping."""

    def __init__(self, host_root: Path, virtual_root: str = VIRTUAL_ROOT):
        self.host_root = Path(host_root).resolve()
        self.virtual_root = virtual_root.rstrip("/")
        self.call_log: list[str] = []

    # ---- path translation -------------------------------------------------
    def _to_host(self, virtual: str) -> Path:
        v = str(virtual).strip()
        if not v.startswith("/"):
            v = f"{self.virtual_root}/{v}"
        if v == self.virtual_root:
            rel = ""
        elif v.startswith(self.virtual_root + "/"):
            rel = v[len(self.virtual_root) + 1:]
        else:
            raise ValueError(
                f"path must be under {self.virtual_root}/ -- got {virtual!r}"
            )
        host = (self.host_root / rel).resolve() if rel else self.host_root
        if not str(host).startswith(str(self.host_root)):
            raise PermissionError(f"path escapes the repo root: {virtual!r}")
        return host

    def _to_virtual(self, host: Path) -> str:
        rel = os.path.relpath(str(host), str(self.host_root))
        return self.virtual_root if rel == "." else f"{self.virtual_root}/{rel}"

    # ---- helpers ----------------------------------------------------------
    def tree(self, path: str = VIRTUAL_ROOT, max_depth: int = 3) -> str:
        """Directory tree listing: dirs first, file sizes shown."""
        self.call_log.append(f"tree(path={path!r}, max_depth={max_depth})")
        root = self._to_host(path)
        if not root.exists():
            return f"tree: no such path: {path}"
        if root.is_file():
            return f"{path} ({root.stat().st_size} bytes) -- a file, not a directory"

        lines: list[str] = [f"{self._to_virtual(root)}/"]
        truncated = False

        def walk(d: Path, depth: int, prefix: str) -> None:
            nonlocal truncated
            if depth > max_depth:
                return
            try:
                entries = sorted(
                    (e for e in d.iterdir() if e.name not in SKIP_DIRS),
                    key=lambda e: (e.is_file(), e.name.lower()),
                )
            except OSError as exc:
                lines.append(f"{prefix}[unreadable: {exc}]")
                return
            for i, e in enumerate(entries):
                if len(lines) >= 600:
                    truncated = True
                    return
                last = i == len(entries) - 1
                branch = "`-- " if last else "|-- "
                if e.is_dir():
                    lines.append(f"{prefix}{branch}{e.name}/")
                    walk(e, depth + 1, prefix + ("    " if last else "|   "))
                else:
                    lines.append(f"{prefix}{branch}{e.name} ({e.stat().st_size})")

        walk(root, 1, "")
        if truncated:
            lines.append(f"... [tree truncated at 600 entries; "
                         f"call tree() on a subdirectory for more]")
        return "\n".join(lines)

    def read(self, path: str, start: int = 1, end: int | None = None) -> str:
        """File contents with line numbers. Caps at 400 lines per call."""
        self.call_log.append(f"read(path={path!r}, start={start}, end={end})")
        p = self._to_host(path)
        if not p.exists():
            return f"read: no such file: {path}"
        if p.is_dir():
            return f"read: {path} is a directory -- use tree({path!r}) instead"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"read: cannot read {path}: {exc}"
        lines = text.split("\n")
        total = len(lines)
        start = max(1, int(start))
        end = total if end is None else min(int(end), total)
        if end - start + 1 > 400:
            end = start + 399
        chunk = lines[start - 1:end]
        width = len(str(end))
        body = "\n".join(
            f"{start + i:>{width}}| {ln}" for i, ln in enumerate(chunk)
        )
        header = f"{path} (lines {start}-{end} of {total})"
        footer = ""
        if end < total:
            footer = f"\n... [{total - end} more lines; call read({path!r}, start={end + 1})]"
        return f"{header}\n{body}{footer}"

    def grep(
        self,
        pattern: str,
        path: str = VIRTUAL_ROOT,
        glob: str = "*",
        context: int = 2,
        max_results: int = 50,
    ) -> str:
        """Regex search across files. rg-style `file:line: text` with context."""
        self.call_log.append(
            f"grep(pattern={pattern!r}, path={path!r}, glob={glob!r}, "
            f"context={context}, max_results={max_results})"
        )
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"grep: bad regex {pattern!r}: {exc}"
        root = self._to_host(path)
        if not root.exists():
            return f"grep: no such path: {path}"

        suffix_filter = None
        if glob and glob != "*":
            g = glob.strip()
            if g.startswith("*."):
                suffix_filter = g[1:]
            elif g.startswith("."):
                suffix_filter = g

        targets: list[Path] = []
        if root.is_file():
            targets = [root]
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for fn in sorted(filenames):
                    if suffix_filter and not fn.endswith(suffix_filter):
                        continue
                    targets.append(Path(dirpath) / fn)

        out: list[str] = []
        n_hits = 0
        files_with_hits = 0
        for f in sorted(targets):
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                continue
            hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
            if not hits:
                continue
            files_with_hits += 1
            vpath = self._to_virtual(f)
            for i in hits:
                if n_hits >= max_results:
                    out.append(
                        f"... [result cap {max_results} reached; "
                        f"{files_with_hits} files matched so far -- "
                        f"narrow with path= or glob=, or raise max_results]"
                    )
                    return "\n".join(out) if out else "grep: no matches"
                n_hits += 1
                lo = max(0, i - context)
                hi = min(len(lines), i + context + 1)
                for j in range(lo, hi):
                    marker = ":" if j == i else "-"
                    out.append(f"{vpath}:{j + 1}{marker} {lines[j]}")
                out.append("--")
        if not out:
            return (
                f"grep: no matches for {pattern!r} under {path}"
                + (f" (glob={glob})" if glob != "*" else "")
            )
        return "\n".join(out) + f"\n[{n_hits} matches in {files_with_hits} files]"

    def as_external_lookup(self) -> dict:
        return {"tree": self.tree, "read": self.read, "grep": self.grep}


PRELUDE_DOC = """\
Three host-provided helper functions are available as plain globals (do NOT
import them, they already exist):

  tree(path='/repo', max_depth=3) -> str
      Directory listing as a tree. Dirs first, file sizes in parens.
      e.g. tree('/repo')            tree('/repo/pydantic_ai_slim', max_depth=2)

  read(path, start=1, end=None) -> str
      File contents with line numbers, max 400 lines per call.
      e.g. read('/repo/train.py')   read('/repo/model.py', 100, 180)

  grep(pattern, path='/repo', glob='*', context=2, max_results=50) -> str
      Regex search. Output is `file:line: text` with context lines.
      e.g. grep('def get_lr')                grep('dropout', glob='*.py')
           grep('class \\\\w+Retry', '/repo/pydantic_ai_slim', '*.py')

They return strings -- print() them, or slice/split them with your own Python
in the same snippet.
"""
