"""Host-side verification of code citations — no LLM in the loop.

We hold the ground-truth bytes the agent read, so a code citation can be
checked exactly: does the quoted snippet appear at the claimed lines? Three
outcomes:

  exact      — quote found inside `lines=A-B`        → keep, attach permalink
                                                       (narrowed to the quote's
                                                       own lines when A-B is more
                                                       than twice as long)
  repaired   — quote found elsewhere in the file     → rewrite the line range
  unverified — quote not in the file at all          → drop the lines, mark
                                                       `verified: false`

Matching is whitespace-normalized and mapped back to line numbers: the
evidence parser flattens a multi-line quote by joining its lines with single
spaces, so a literal comparison against the file would fail on every quote
longer than one line.

(Contrast with PDF citations, which need OCR↔pymupdf reconciliation and a
FAST-model fallback because no exact ground truth exists.)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.llm.repo.ingest import github_blob_url
from app.llm.repo.sandbox import RepoSnapshot
from app.llm.repo.storage import SnapshotPathError, resolve_within

logger = logging.getLogger(__name__)

# Quotes shorter than this match too loosely to trust a repair.
MIN_QUOTE_CHARS = 8
# Files past this size are not worth a full normalized scan per citation.
MAX_VERIFY_BYTES = 2 * 1024 * 1024

_WS_RE = re.compile(r"\s+")
# Models routinely emit a code quote with JSON-string escaping: newlines as
# literal `\n`, inner quotes as `\"`. None of that matches the real file, so
# a second comparison pass decodes the escapes first.
#
# One pass, so `\\n` (an escaped backslash followed by `n`) decodes to a
# literal backslash plus `n` rather than to a newline. Unknown escapes —
# `\d`, `\s` in a quoted regex — are left exactly as they were.
_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
# `\n` decodes to a REAL newline, not a space: whitespace normalization
# collapses it either way, but the line structure has to survive long enough
# for the line-number gutter to be stripped per line.
_ESCAPE_MAP = {"n": "\n", "r": "\n", "t": " ", '"': '"', "'": "'", "\\": "\\"}


def _unescape(text: str) -> str:
    return _ESCAPE_RE.sub(
        lambda match: _ESCAPE_MAP.get(match.group(1), match.group(0)), text
    )


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


# A long code quote is often abridged: the model quotes the head of the span
# and marks the rest with an ellipsis. The head is still ground truth, so it
# is verified on its own rather than throwing the citation away.
_ELISION_RE = re.compile(r"(?:\.\s*\.\s*\.|…)")
MIN_ELIDED_PREFIX_CHARS = 40


def _elided_prefix(quote: str) -> Optional[str]:
    """The verifiable head of an abridged quote, or None."""
    match = _ELISION_RE.search(quote)
    if match is None:
        return None
    prefix = quote[: match.start()].strip()
    return prefix if len(_normalize(prefix)) >= MIN_ELIDED_PREFIX_CHARS else None


# The prelude's `read()` prints a line-number gutter (`  52| code`), and
# models quote its output verbatim. Such a quote can NEVER match the raw file
# bytes, so the gutter is stripped before matching — and the first line's
# number becomes a strong hint for where the span starts.
_GUTTER_RE = re.compile(r"^[ \t]*(\d{1,7})[ \t]*\|[ \t]?")


# A gutter that survived the evidence parser's line-joining: digits glued to
# the pipe (`read()` prints `NNN| `), preceded by whitespace or the start.
_INLINE_GUTTER_RE = re.compile(r"(?:(?<=\s)|^)(\d{1,7})\|[ \t]?")


def _split_inline_gutters(text: str) -> Optional[List[str]]:
    """Re-split a flattened gutter quote into its lines, or None.

    `evidence.parse_evidence_block` joins a quote's lines with single
    spaces, so a multi-line `read()` quote reaches verification as ONE line:
    `12| foo 13| bar`. Recognized only when the quote STARTS with a gutter
    and the numbers strictly increase — `a = 1|2` never qualifies.
    """
    stripped = text.strip()
    matches = list(_INLINE_GUTTER_RE.finditer(stripped))
    if len(matches) < 2 or matches[0].start() != 0:
        return None
    numbers = [int(match.group(1)) for match in matches]
    if any(later <= earlier for earlier, later in zip(numbers, numbers[1:])):
        return None
    lines: List[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
        lines.append(stripped[match.start() : end].rstrip())
    return lines


def _strip_line_gutter(text: str) -> Tuple[Optional[str], Optional[int]]:
    """(text without the `NNN| ` prefixes, first line number) or (None, None).

    Requires a MAJORITY of non-empty lines to carry the gutter so ordinary
    code (`flags = READ | WRITE`) or a markdown table is never mangled.
    """
    lines = text.split("\n")
    if len(lines) == 1:
        inline = _split_inline_gutters(text)
        if inline is not None:
            lines = inline
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return None, None
    matches = [_GUTTER_RE.match(line) for line in non_empty]
    hits = [match for match in matches if match is not None]
    if not hits or len(hits) * 2 <= len(non_empty):
        return None, None
    stripped = "\n".join(_GUTTER_RE.sub("", line) for line in lines)
    try:
        first_number = int(hits[0].group(1))
    except (TypeError, ValueError):
        first_number = None
    return stripped, first_number


def _quote_forms(quote: str) -> List[Tuple[str, Optional[int]]]:
    """(text, start-line hint) forms to try, most faithful first.

    The raw form goes first so a file that genuinely contains the two-char
    sequence `\\n` (inside a Python string literal, say) still matches
    exactly; escape-decoded and gutter-stripped forms follow.
    """
    sources = [quote]
    if "\\" in quote:
        decoded = _unescape(quote)
        if decoded != quote:
            sources.append(decoded)

    forms: List[Tuple[str, Optional[int]]] = []
    seen: set = set()

    def _add(text: str, hint: Optional[int]) -> None:
        normalized = _normalize(text)
        if not normalized or (normalized, hint) in seen:
            return
        seen.add((normalized, hint))
        forms.append((normalized, hint))

    for source in sources:
        _add(source, None)
        stripped, hint = _strip_line_gutter(source)
        if stripped is not None:
            _add(stripped, hint)
    return forms


def _candidate_quotes(quote: str) -> List[str]:
    """Normalized text forms only (no hints) — used for elided prefixes."""
    return [text for text, _ in _quote_forms(quote)]


def _normalized_index(lines: List[str]) -> Tuple[str, List[int]]:
    """Flatten `lines` to a single normalized string and record, for every
    character in it, the 1-indexed source line it came from."""
    parts: List[str] = []
    owners: List[int] = []
    for number, line in enumerate(lines, start=1):
        normalized = _normalize(line)
        if not normalized:
            continue
        if parts:
            parts.append(" ")
            owners.append(number)
        parts.append(normalized)
        owners.extend([number] * len(normalized))
    return "".join(parts), owners


class _FileCache:
    """Per-batch cache of file lines and their normalized index.

    Ten citations on one file would otherwise mean ten reads and ten full
    normalizations of up to MAX_VERIFY_BYTES, synchronously on the event loop.
    """

    def __init__(self) -> None:
        self.lines: Dict[str, Optional[List[str]]] = {}
        self.index: Dict[str, Tuple[str, List[int]]] = {}

    def get_lines(self, snapshot: RepoSnapshot, path: str) -> Optional[List[str]]:
        if path not in self.lines:
            self.lines[path] = _read_file(snapshot, path)
        return self.lines[path]

    def get_index(self, path: str, lines: List[str]) -> Tuple[str, List[int]]:
        if path not in self.index:
            self.index[path] = _normalized_index(lines)
        return self.index[path]


def _read_file(snapshot: RepoSnapshot, path: str) -> Optional[List[str]]:
    known = {
        str(entry.get("path"))
        for entry in snapshot.files
        if isinstance(entry, dict) and entry.get("path")
    }
    rel = str(path or "").strip().lstrip("/")
    if rel.startswith("repo/"):
        rel = rel[len("repo/") :]
    if rel not in known:
        return None
    try:
        host = resolve_within(snapshot.root, rel)
        if host.stat().st_size > MAX_VERIFY_BYTES:
            return None
        return host.read_text(encoding="utf-8", errors="replace").split("\n")
    except (OSError, SnapshotPathError):
        return None


def normalize_citation_path(snapshot: RepoSnapshot, path: str) -> Optional[str]:
    """Map whatever the model wrote to a manifest path, or None."""
    known = [
        str(entry.get("path"))
        for entry in snapshot.files
        if isinstance(entry, dict) and entry.get("path")
    ]
    rel = str(path or "").strip().lstrip("/")
    if rel.startswith("repo/"):
        rel = rel[len("repo/") :]
    if not rel:
        return None
    if rel in known:
        return rel
    # Models sometimes cite a bare basename or a partial path; accept it when
    # exactly one manifest entry ends with it.
    matches = [entry for entry in known if entry.endswith("/" + rel)]
    if len(matches) == 1:
        return matches[0]
    return None


def _find_within_lines(
    flat: str,
    owners: List[int],
    candidates: List[str],
    start_line: int,
    end_line: int,
) -> Optional[Tuple[int, int]]:
    """(first, last) source lines of the first candidate found INSIDE the
    claimed `[start_line, end_line]` range of the normalized index, or None."""
    for candidate in candidates:
        if not candidate:
            continue
        position = flat.find(candidate)
        while position != -1:
            last = min(position + len(candidate) - 1, len(owners) - 1)
            first_line, last_line = owners[position], owners[last]
            if first_line >= start_line and last_line <= end_line:
                return first_line, last_line
            if first_line > end_line:
                break
            position = flat.find(candidate, position + 1)
    return None


def _match_anchored_at(
    lines: List[str], text: str, start: int
) -> Optional[int]:
    """If `text` starts exactly at line `start`, return its END line.

    Used for the gutter hint: the model quoted `read()` output, so the line
    number it printed is where the span really begins — verify that and map
    the end of the quote back to a real line.
    """
    if start < 1 or start > len(lines):
        return None
    flat, owners = _normalized_index(lines[start - 1 :])
    if not flat.startswith(text):
        return None
    end_position = min(len(text) - 1, len(owners) - 1)
    if end_position < 0:
        return None
    return start - 1 + owners[end_position]


def verify_code_citation(
    citation: Dict[str, Any],
    snapshot: RepoSnapshot,
    cache: Optional["_FileCache"] = None,
) -> Dict[str, Any]:
    """Return a NEW citation dict with verification applied.

    Never mutates the input (immutability: callers keep the raw citation for
    the first `data-citations` emission).
    """
    cache = cache if cache is not None else _FileCache()
    out: Dict[str, Any] = dict(citation)
    raw_path = str(out.get("file") or "")
    resolved = normalize_citation_path(snapshot, raw_path)
    if resolved is None:
        return _unverified(out, snapshot)
    out["file"] = resolved

    lines = cache.get_lines(snapshot, resolved)
    if lines is None:
        return _unverified(out, snapshot)

    raw_quote = str(out.get("reference") or "").strip().strip('"').strip("'")
    forms = _quote_forms(raw_quote)
    candidates = [text for text, _ in forms]
    claimed_start, claimed_end = _clamped_range(out, len(lines))

    if not any(len(candidate) >= MIN_QUOTE_CHARS for candidate in candidates):
        # Too short to verify by CONTENT. An in-bounds line range is not
        # evidence — `"x"` would otherwise "verify" any range in the file and
        # earn a permalink that says we checked it. We did not.
        return _unverified(out, snapshot)

    # 1. The range the model claimed. Kept when the quote covers at least half
    #    of it (a quote is usually an excerpt of the span it cites); narrowed
    #    to the quote's own lines otherwise — a two-line quote under
    #    `lines=1-400` must not earn a 400-line highlight and permalink.
    if claimed_start is not None:
        flat, owners = cache.get_index(resolved, lines)
        span = _find_within_lines(flat, owners, candidates, claimed_start, claimed_end)
        if span is not None:
            first, last = span
            if claimed_end - claimed_start + 1 > 2 * (last - first + 1):
                return _verified(out, snapshot, first, last)
            return _verified(out, snapshot, claimed_start, claimed_end)

    # 2. The range implied by a quoted `read()` gutter — authoritative when
    #    the quote really does start there, and it fixes a wrong or missing
    #    `lines=` marker.
    for text, hint in forms:
        if hint is None or len(text) < MIN_QUOTE_CHARS:
            continue
        end_line = _match_anchored_at(lines, text, hint)
        if end_line is not None:
            return _verified(
                out, snapshot, hint, end_line, matched_via="line-numbered"
            )

    # Abridged quote ("head of the span ..."): verify the head alone. The
    # claimed range is kept when the head sits at its start — the elision
    # means the span continues past what was quoted.
    prefix = _elided_prefix(raw_quote)
    prefix_candidates = _candidate_quotes(prefix) if prefix else []
    if prefix_candidates and claimed_start is not None:
        window = _normalize(" ".join(lines[claimed_start - 1 : claimed_end]))
        if any(candidate in window for candidate in prefix_candidates):
            return _verified(
                out, snapshot, claimed_start, claimed_end, matched_via="elided"
            )

    # Repair: search the whole file for the quote and map back to lines.
    flat, owners = cache.get_index(resolved, lines)
    for candidate in candidates:
        if len(candidate) < MIN_QUOTE_CHARS:
            continue
        position = flat.find(candidate)
        if position == -1:
            continue
        end_position = min(position + len(candidate) - 1, len(owners) - 1)
        return _verified(
            out, snapshot, owners[position], owners[end_position], repaired=True
        )
    for candidate in prefix_candidates:
        position = flat.find(candidate)
        if position == -1:
            continue
        end_position = min(position + len(candidate) - 1, len(owners) - 1)
        return _verified(
            out, snapshot, owners[position], owners[end_position],
            matched_via="elided-repaired",
        )
    return _unverified(out, snapshot)


def _clamped_range(
    citation: Dict[str, Any], total: int
) -> Tuple[Optional[int], int]:
    try:
        start = int(citation.get("start_line") or 0)
    except (TypeError, ValueError):
        return None, 0
    if start < 1 or start > total:
        return None, 0
    try:
        end = int(citation.get("end_line") or start)
    except (TypeError, ValueError):
        end = start
    end = max(start, min(end, total))
    return start, end


def _verified(
    citation: Dict[str, Any],
    snapshot: RepoSnapshot,
    start: int,
    end: int,
    *,
    repaired: bool = False,
    matched_via: Optional[str] = None,
) -> Dict[str, Any]:
    out = dict(citation)
    out["start_line"] = start
    out["end_line"] = end
    out["verified"] = True
    out["github_url"] = github_blob_url(
        snapshot.owner, snapshot.repo, snapshot.commit_sha, out["file"], start, end
    )
    out["matched_via"] = matched_via or ("repaired" if repaired else "exact")
    return out


def _unverified(
    citation: Dict[str, Any], snapshot: Optional[RepoSnapshot] = None
) -> Dict[str, Any]:
    """Drop the line anchor, the permalink, and mark unverified.

    Binding revision 7: `github_url` is attached ONLY after verification. A
    citation we could not confirm gets no link at all — not even a file-level
    one, which would still read as "we checked this".
    """
    out = dict(citation)
    out.pop("start_line", None)
    out.pop("end_line", None)
    out["verified"] = False
    out["github_url"] = None
    if snapshot is not None:
        resolved = normalize_citation_path(snapshot, str(out.get("file") or ""))
        if resolved:
            out["file"] = resolved
    return out


def verify_code_citations(
    citations: List[Dict[str, Any]], snapshot: Optional[RepoSnapshot]
) -> List[Dict[str, Any]]:
    """Verify every citation carrying a `file` extra; pass others through."""
    out: List[Dict[str, Any]] = []
    cache = _FileCache()
    for citation in citations:
        if not citation.get("file"):
            out.append(dict(citation))
            continue
        if snapshot is None:
            out.append(_unverified(citation))
            continue
        try:
            out.append(verify_code_citation(citation, snapshot, cache))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Code citation verification failed: %s", exc)
            # Same rule on the error path: no line anchor, no permalink.
            out.append(_unverified(citation))
    return out
