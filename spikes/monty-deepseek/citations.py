"""Score citation accuracy in a model's final answer against the repo on disk.

Production intent: citations become GitHub permalinks
`blob/{sha}/{path}#L{a}-L{b}`, with line numbers verified and repaired
host-side against the file bytes. So the categories that matter are:

  exact       -- file exists AND the quoted snippet sits at the cited lines
  repairable  -- file exists AND the snippet is in the file, but at other
                 lines (host-side repair fixes the permalink)
  fabricated  -- the file does not exist, or the quoted snippet is nowhere in
                 the cited file (nothing to repair; this is the failure mode)
  unverified  -- a citation with no accompanying quote; we can only check that
                 the file exists and the range is in bounds
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

# `path/to/file.py lines 12-34`, `path/to/file.py line 12`, `path/to/file.py:12-34`
CITATION_RE = re.compile(
    r"""
    `?
    (?P<path>(?:/?repo/)?[\w.\-/]+\.(?:py|md|ipynb|toml|yaml|yml|txt|cfg|sh|json))
    `?
    [\s,]*
    (?:
        (?:,\s*)?\b(?:lines?|L)\s*(?P<a1>\d+)\s*(?:[-–—]\s*(?:L)?(?P<b1>\d+))?
      | :\s*(?P<a2>\d+)\s*(?:[-–—]\s*(?P<b2>\d+))?
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)


@dataclass
class Citation:
    raw: str
    path: str
    start: int
    end: int
    verdict: str
    detail: str = ""
    found_at: int | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _candidate_quote(answer: str, cite_start: int, cite_end: int) -> str | None:
    """The code the citation refers to.

    Three shapes occur in practice, and getting this wrong silently turns
    correct citations into "fabricated" ones:

      1. citation line INSIDE the fence, as its first line -- the shape
         DeepSeek prefers:
             ```python
             model.py lines 126-132
                 wte = nn.Embedding(...)
             ```
      2. citation on its own line, fence immediately after (the shape asked
         for in the system prompt)
      3. fence immediately before the citation
    """
    # (1) is the citation inside a fenced block?
    for m in FENCE_RE.finditer(answer):
        inner_start, inner_end = m.start(1), m.end(1)
        if inner_start <= cite_start < inner_end:
            body = m.group(1)
            # A block carrying SEVERAL citations is a summary/flow diagram,
            # not a quotation of one span -- there is no single snippet to
            # verify against, so don't judge it as a bad quote.
            n_cites = len({
                (c.group("path"), c.group("a1") or c.group("a2"))
                for c in CITATION_RE.finditer(body)
            })
            if n_cites > 1:
                return None
            keep = [ln for ln in body.split("\n") if not CITATION_RE.search(ln)]
            joined = "\n".join(keep).strip()
            return joined or None

    # (2) a fence that ends JUST before the citation -- "```\n`train.py` lines
    # 76-78" is DeepSeek's dominant shape, and it must win over the fence that
    # opens further down (which belongs to the next section).
    before = None
    for m in FENCE_RE.finditer(answer[:cite_start]):
        before = m
    gap_before = cite_start - before.end() if before else 10**9

    after = FENCE_RE.search(answer, cite_end)
    gap_after = after.start() - cite_end if after else 10**9

    if gap_before <= 120:
        return before.group(1)
    if gap_after < 400:
        return after.group(1)
    if gap_before < 400:
        return before.group(1)
    return None


def _resolve(repo_root: Path, path: str) -> Path | None:
    p = path.lstrip("/")
    if p.startswith("repo/"):
        p = p[len("repo/"):]
    direct = repo_root / p
    if direct.is_file():
        return direct
    # tolerate a partially-qualified path by matching on the tail
    tail = Path(p).name
    matches = [
        f for f in repo_root.rglob(tail)
        if f.is_file() and str(f.relative_to(repo_root)).endswith(p)
    ]
    return matches[0] if len(matches) == 1 else None


def score_answer(answer: str, repo_root: Path) -> tuple[list[Citation], dict]:
    repo_root = Path(repo_root)
    cites: list[Citation] = []
    seen: set[tuple[str, int, int]] = set()

    for m in CITATION_RE.finditer(answer):
        path = m.group("path")
        a = m.group("a1") or m.group("a2")
        b = m.group("b1") or m.group("b2")
        start = int(a)
        end = int(b) if b else start
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)

        f = _resolve(repo_root, path)
        if f is None:
            cites.append(Citation(m.group(0), path, start, end, "fabricated",
                                  "file does not exist in the repo"))
            continue

        lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
        if start > len(lines):
            cites.append(Citation(m.group(0), path, start, end, "fabricated",
                                  f"line {start} > file length {len(lines)}"))
            continue

        quote = _candidate_quote(answer, m.start(), m.end())
        if not quote:
            cites.append(Citation(m.group(0), path, start, end, "unverified",
                                  f"no quoted snippet; range in bounds "
                                  f"(file has {len(lines)} lines)"))
            continue

        qlines = [_norm(l) for l in quote.split("\n") if _norm(l)]
        if not qlines:
            cites.append(Citation(m.group(0), path, start, end, "unverified",
                                  "empty code block"))
            continue
        # anchor on the longest quoted line -- most distinctive
        anchor = max(qlines, key=len)
        norm_file = [_norm(l) for l in lines]

        hits = [i + 1 for i, l in enumerate(norm_file) if l and anchor in l]
        if not hits:
            cites.append(Citation(m.group(0), path, start, end, "fabricated",
                                  f"quoted line not found in file: {anchor[:70]!r}"))
            continue

        in_range = [h for h in hits if start - 3 <= h <= end + 3]
        if in_range:
            cites.append(Citation(m.group(0), path, start, end, "exact",
                                  f"anchor found at line {in_range[0]}",
                                  found_at=in_range[0]))
        else:
            cites.append(Citation(m.group(0), path, start, end, "repairable",
                                  f"anchor actually at line(s) {hits[:4]}",
                                  found_at=hits[0]))

    counts = {"exact": 0, "repairable": 0, "fabricated": 0, "unverified": 0}
    for c in cites:
        counts[c.verdict] += 1
    counts["total"] = len(cites)
    return cites, counts


def render(cites: list[Citation]) -> str:
    if not cites:
        return "  (no citations found in the answer)"
    return "\n".join(
        f"  [{c.verdict:10s}] {c.path} lines {c.start}-{c.end} -- {c.detail}"
        for c in cites
    )


def to_dicts(cites: list[Citation]) -> list[dict]:
    return [asdict(c) for c in cites]
