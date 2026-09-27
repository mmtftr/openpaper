"""Page furniture and footnotes in OCR'd pages → clean body text + GFM footnotes.

Mistral OCR is asked for `extract_header` / `extract_footer` /
`include_blocks` (`sources.mistral.request_body`). Then a page's `markdown`
comes back without its running header and footer; those are in the page's
`header` / `footer` strings, and `blocks` lists every layout block (`type`
∈ text, title, footer, header, caption, image, table, list, equation, code,
references, aside_text; px box in the page image's `dimensions`). There is
no footnote type: footnotes come back as `footer` blocks, or — when they sit
at the bottom of a column (e.g. ICML's affiliation note) — as ordinary
`text` blocks. Three steps turn that into GFM footnotes:

1. `split_page(markdown, page)` — per page, in the `ocr` stage. Footer
   blocks that aren't page numbers, and marker-led `text` blocks at the
   bottom of a column (plus what follows them there), become notes; a block
   that holds several notes (an affiliation list) is split at its inline
   markers. Where a note's marker (`\\( ^{1} \\)`, `${}^{1}$`, `¹`, `†`, ...)
   occurs in the page's text it becomes a `[^k]` reference; each note is
   appended to the page as a `[^k]: text` definition (k page-local),
   referenced or not. Headers are dropped (still in the stored payload).
2. `renumber(pages)` — whole paper, in `ocr_repair` (which writes the final
   text): labels become `1..n`, unique across the paper, in order of first
   appearance; unreferenced notes whose text recurs on several pages
   (running footers: "Preprint.", "Nature Communications | (2026)17:5926")
   are dropped.
3. `collect_definitions(markdown)` — the markdown API moves every definition
   to the end of the assembled document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# -- markers --------------------------------------------------------------------

_LATEX_SYMBOLS = {
    r"\ast": "*",
    r"\star": "*",
    r"\dagger": "†",
    r"\ddagger": "‡",
    r"\S": "§",
    r"\P": "¶",
    r"\sharp": "#",
}
_SYMBOL = r"\\(?:ast|star|dagger|ddagger|sharp|S|P)(?![A-Za-z])|\*+|†+|‡+|§+|¶+|#+"
_PART = rf"(?:\d{{1,3}}|[a-z]|{_SYMBOL})"
# One or more marker parts: "1", "1*", "1,2", "\dagger".
_TOKEN = rf"{_PART}(?:\s*,?\s*{_PART})*"
_PART_RE = re.compile(rf"\d{{1,3}}|[a-z]|{_SYMBOL}")

# A superscript used as a footnote marker, in the forms OCR writes it.
_SUP = (
    rf"\\\(\s*(?:\{{\}})?\s*\^\s*(?:\{{\s*(?P<a>{_TOKEN})\s*\}}|(?P<b>{_PART}))\s*\\\)"
    rf"|\$\s*(?:\{{\}})?\s*\^\s*(?:\{{\s*(?P<c>{_TOKEN})\s*\}}|(?P<d>{_PART}))\s*\$"
    rf"|<sup>\s*(?P<e>{_TOKEN})\s*</sup>"
    r"|(?P<f>[⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
)
_SUP_RE = re.compile(_SUP)
_UNICODE_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

# A note's leading marker: a superscript, a note symbol, or (footer lines
# only) a bare number followed by text ("1 Code available at ...").
_LEAD_SUP_RE = re.compile(rf"\s*(?:{_SUP})")
_LEAD_SYMBOL_RE = re.compile(r"\s*(?P<s>\*(?![\s*])|[†‡§¶](?!\s*$))")
_LEAD_NUMBER_RE = re.compile(r"\s*(?P<n>\d{1,3})(?=\s+\S)")
# "*emphasis*" at the start of a paragraph is markdown, not a note marker.
_EMPHASIS_RE = re.compile(r"\s*\*[^*\n]+\*")

# Inside a note block, where another note starts: a marker right after
# whitespace and directly followed by text ("... London \( ^{4} \)Fraunhofer").
_INLINE_START_RE = re.compile(rf"(?<=\s)(?:{_SUP}|[†‡§¶])(?=[^\s,.;:)\]])")

# A footer line that is only a page number ("3", "iv", "Page 3 of 20").
_PAGE_NUMBER_RE = re.compile(
    r"^[\s\-–—|]*(?:page\s*)?(?:\d{1,4}|[ivxlcdm]{1,7})"
    r"(?:\s*(?:of|/)\s*\d{1,4})?[\s\-–—|]*$",
    re.IGNORECASE,
)

# GFM footnotes as this module writes them.
_LABEL_RE = re.compile(r"\[\^([^\]\s]+)\]")
_DEFINITION_RE = re.compile(r"(?m)^\[\^([^\]\s]+)\]:[ \t]*(.*)$")
# A definition line plus the blank lines after it (for removal).
_DEFINITION_LINE_RE = re.compile(r"(?m)^\[\^[^\]\s]+\]:[^\n]*(?:\n+|$)")

# Where a marker-led `text` block may be a footnote: its top in the bottom
# part of the page; later blocks join its note area when they start within
# this gap below it (fractions of the page height).
_NOTE_AREA_TOP = 0.55
_NOTE_AREA_GAP = 0.02


# What a reference marker can't follow (then it's an exponent).
_NOT_AFTER_RE = re.compile(r"(?:\d|\\\)|\$)\s*$")


def _parts(token: str) -> list[str]:
    """ "1*" → ["1", "*"]; "1, 2" → ["1", "2"]; "\\dagger" → ["†"]."""
    return [_LATEX_SYMBOLS.get(p) or p for p in _PART_RE.findall(token)]


def _sup_token(m: re.Match[str]) -> str:
    for group in "abcde":
        if m.group(group):
            return m.group(group)
    return (m.group("f") or "").translate(_UNICODE_DIGITS)


# -- page level ----------------------------------------------------------------


@dataclass(frozen=True)
class Note:
    marker: Optional[str]  # "1", "*", "†" ... (None: no marker found)
    text: str


def _lead_marker(text: str, *, allow_number: bool) -> tuple[Optional[str], str]:
    """(marker, rest) when `text` starts with a note marker, else (None, text)."""
    m = _LEAD_SUP_RE.match(text)
    if m:
        parts = _parts(_sup_token(m))
        if len(parts) == 1:
            return parts[0], text[m.end() :]
        return None, text
    m = _LEAD_SYMBOL_RE.match(text)
    if m and not _EMPHASIS_RE.match(text):
        return m.group("s"), text[m.end() :]
    if allow_number:
        m = _LEAD_NUMBER_RE.match(text)
        if m:
            return m.group("n"), text[m.end() :]
    return None, text


def _has_lead_marker(text: str) -> bool:
    return _lead_marker(text, allow_number=False)[0] is not None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _notes_in(text: str, *, allow_number: bool) -> list[Note]:
    """One footnote-area block → its notes (split at inline note markers)."""
    starts = [0] + [m.start() for m in _INLINE_START_RE.finditer(text)]
    notes = []
    for start, end in zip(starts, starts[1:] + [len(text)]):
        chunk = text[start:end]
        marker, rest = _lead_marker(chunk, allow_number=allow_number and start == 0)
        rest = _clean(rest)
        if rest:
            notes.append(Note(marker, rest))
    return notes


Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (px, top-left origin)


def _box(block: dict[str, Any]) -> Optional[Box]:
    coords = [
        block.get(k)
        for k in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")
    ]
    if not all(isinstance(c, (int, float)) for c in coords):
        return None
    x0, y0, x1, y1 = (float(c) for c in coords)  # type: ignore[arg-type]
    return x0, y0, x1, y1


def _note_area_blocks(blocks: list[dict[str, Any]], height: float) -> list[str]:
    """Contents of body `text` blocks that are footnotes at a column's bottom.

    A marker-led text block in the lower part of the page starts a note
    area if every block below it in its column is a text block that is
    either marker-led too or starts right below the previous one ("Proceedings
    of ... Copyright ..." under ICML's affiliation note).
    """
    body: list[tuple[Box, str, str]] = []  # (box, type, content)
    for block in blocks:
        box = _box(block)
        kind = str(block.get("type") or "")
        if box is not None and kind not in ("header", "footer"):
            body.append((box, kind, str(block.get("content") or "")))
    body.sort(key=lambda b: b[0][1])

    found: set[int] = set()
    for i, (box, kind, content) in enumerate(body):
        if (
            kind != "text"
            or box[1] < _NOTE_AREA_TOP * height
            or not _has_lead_marker(content)
        ):
            continue
        area, last_bottom = [i], box[3]
        for j in range(i + 1, len(body)):
            other, other_kind, text = body[j]
            if min(box[2], other[2]) <= max(box[0], other[0]):
                continue  # another column
            if other_kind != "text" or not (
                _has_lead_marker(text)
                or other[1] - last_bottom <= _NOTE_AREA_GAP * height
            ):
                break
            area.append(j)
            last_bottom = other[3]
        else:
            found.update(area)
    return [body[j][2] for j in sorted(found)]


def _remove_paragraph(markdown: str, text: str) -> tuple[str, bool]:
    """Remove `text` where it is a whole paragraph of `markdown` (last one)."""
    text = text.strip()
    if not text:
        return markdown, False
    matches = list(
        re.finditer(rf"(?:^|\n\n){re.escape(text)}[ \t]*(?=\n\n|\n?$)", markdown)
    )
    if not matches:
        return markdown, False
    before = markdown[: matches[-1].start()].rstrip("\n")
    after = markdown[matches[-1].end() :].lstrip("\n")
    if before and after:
        return f"{before}\n\n{after}", True
    return before or after, True


def _link_references(body: str, labels: dict[str, str]) -> str:
    """Replace note markers in `body` with `[^label]` references."""

    def link(m: re.Match[str]) -> str:
        parts = _parts(_sup_token(m))
        if not parts or not all(p in labels for p in parts):
            return m.group(0)
        # "10 \( ^{3} \)" is 10³, "\( x \) \( ^{2} \)" is x²: not references.
        if _NOT_AFTER_RE.search(body[max(0, m.start() - 8) : m.start()]):
            return m.group(0)
        return "".join(f"[^{labels[p]}]" for p in parts)

    return _SUP_RE.sub(link, body)


def split_page(markdown: str, page: dict[str, Any]) -> str:
    """One Mistral page (with `header` / `footer` / `blocks`) → its body text
    with footnote references, and the notes as `[^k]: ...` definitions at
    the end (k = 1.. within the page; `renumber` makes them paper-unique).

    Only removes what it can find as whole paragraphs; with no footer and no
    note area the markdown comes back unchanged.
    """
    body = markdown
    blocks = [b for b in page.get("blocks") or [] if isinstance(b, dict)]
    dimensions = page.get("dimensions")
    height = dimensions.get("height") if isinstance(dimensions, dict) else None

    # A header/footer Mistral left in the text anyway (older responses).
    header = page.get("header")
    if isinstance(header, str):
        for line in header.split("\n"):
            body, _ = _remove_paragraph(body, line)

    notes: list[Note] = []
    if isinstance(height, (int, float)) and height > 0:
        for content in _note_area_blocks(blocks, float(height)):
            body, removed = _remove_paragraph(body, content)
            if removed:
                notes += _notes_in(content, allow_number=False)

    footer_blocks = [
        str(b.get("content") or "") for b in blocks if b.get("type") == "footer"
    ]
    footer = page.get("footer")
    if not footer_blocks and isinstance(footer, str):
        footer_blocks = footer.split("\n")
    for item in footer_blocks:
        body, _ = _remove_paragraph(body, item)
        if item.strip() and not _PAGE_NUMBER_RE.match(item):
            notes += _notes_in(item, allow_number=True)

    if not notes:
        return body
    labels: dict[str, str] = {}
    for k, note in enumerate(notes, start=1):
        if note.marker is not None:
            labels.setdefault(note.marker, str(k))
    body = _link_references(body, labels)
    definitions = "\n\n".join(
        f"[^{k}]: {note.text}" for k, note in enumerate(notes, start=1)
    )
    return f"{body.rstrip()}\n\n{definitions}" if body.strip() else definitions


# -- paper level ---------------------------------------------------------------


def _running_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _split_definitions(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """(text without its definition lines, [(label, note text)])."""
    body = _DEFINITION_LINE_RE.sub("", markdown).rstrip()
    return body, _DEFINITION_RE.findall(markdown)


def _join(body: str, definitions: Sequence[tuple[str, str]]) -> str:
    return "\n\n".join(
        ([body] if body else [])
        + [f"[^{label}]: {text}" for label, text in definitions]
    )


def renumber(pages: Sequence[str]) -> list[str]:
    """Make footnote labels unique across the paper's pages (`1..n`, in order
    of first reference, then unreferenced notes), dropping unreferenced notes
    that recur on several pages (running footers). Labels are scoped to
    their page on input; each page's definitions end up in label order."""
    split = [_split_definitions(markdown) for markdown in pages]
    unreferenced: list[list[tuple[str, str]]] = []
    for body, definitions in split:
        referenced = set(_LABEL_RE.findall(body))
        unreferenced.append([d for d in definitions if d[0] not in referenced])
    seen_on: dict[str, int] = {}
    for notes in unreferenced:
        for key in {_running_key(text) for _, text in notes}:
            seen_on[key] = seen_on.get(key, 0) + 1

    out: list[str] = []
    counter = 0
    for markdown, (body, definitions), notes in zip(pages, split, unreferenced):
        if not definitions:
            out.append(markdown)
            continue
        running = {label for label, text in notes if seen_on[_running_key(text)] > 1}
        kept = [(label, text) for label, text in definitions if label not in running]
        mapping: dict[str, str] = {}

        def number(label: str) -> str:
            nonlocal counter
            if label not in mapping:
                counter += 1
                mapping[label] = str(counter)
            return mapping[label]

        # Only labels defined on the page: "[^a-z]" in a code block stays.
        defined = {label for label, _ in kept}
        body = _LABEL_RE.sub(
            lambda m: (
                f"[^{number(m.group(1))}]" if m.group(1) in defined else m.group(0)
            ),
            body,
        )
        renamed = [(number(label), text) for label, text in kept]
        out.append(_join(body, sorted(renamed, key=lambda d: int(d[0]))))
    return out


def collect_definitions(markdown: str) -> str:
    """Move every `[^n]: ...` definition line to the end of `markdown`."""
    body, definitions = _split_definitions(markdown)
    return _join(body, definitions) if definitions else markdown
