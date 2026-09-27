"""Re-join paragraphs that a page or column break cut mid-sentence.

The markdown API serves a paper as its pages' OCR text joined together, so
a paragraph that runs over a page break arrives as two paragraphs — often
with the page's figure and caption, a standalone page number or a running
header (older ingests keep those in the text) between the halves:

    … Goal attribution has been studied across a
    ![img-0.jpeg](…)
    Figure 1. Overview of our goal-directedness analysis. …
    wide range of fields, including philosophy …

`rejoin(markdown)` joins such halves and moves the figures that sat between
them to just after the joined paragraph. It runs at read time (the markdown
API), so it applies to every paper without re-ingesting it, and it is
deliberately conservative; a join needs all of:

- the first half is a prose paragraph that doesn't end a sentence (no
  terminal punctuation, or an abbreviation like "et al." / "Fig.");
- between the halves there is only page furniture — standalone page
  numbers, running headers (a short line repeated ≥ 3 times in the paper),
  images, figure/table captions, tables, footnote definitions — and no caption that is itself cut off
  (the text after it may be the caption's rest);
- the second half is a prose paragraph that starts in lowercase — and, past
  a figure, is long enough not to be text OCR'd out of the figure.

Headings, lists, tables, code, display math and block quotes are never
joined or altered; page numbers and running headers are dropped only from a
gap that got joined over, and nothing else is ever dropped. A hyphen that
broke a word ("evalu-" + "ation") is removed unless the paper spells the
word with the hyphen elsewhere ("fine-" + "tuning").

Old ingests also left footnotes in the text; this can't tell those from
body text, so a gap holding one isn't joined here. The client's
`liftFootnotes` (`client/src/lib/markdownFootnotes.ts`) lifts them and joins
only across a gap with a note it lifted, so the two don't overlap.

Chat and search (`content.py`) read the per-page text, not this: joining
would move text and figures across pages and break the per-page offsets
their citations point at.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# -- blocks ---------------------------------------------------------------------


@dataclass
class _Block:
    text: str
    kind: str  # paragraph heading image table code math list rule note other


_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_IMAGE_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)\s*$")


def _split_blocks(markdown: str) -> list[_Block]:
    """Blank-line separated blocks; fenced code and display math stay whole."""
    blocks: list[_Block] = []
    lines: list[str] = []
    fence: str | None = None
    in_math = False

    def flush() -> None:
        if lines:
            text = "\n".join(lines)
            blocks.append(_Block(text, _kind(text)))
            lines.clear()

    for line in markdown.split("\n"):
        stripped = line.strip()
        if fence:
            lines.append(line)
            if stripped.startswith(fence):
                fence = None
            continue
        if in_math:
            lines.append(line)
            if re.search(r"(\$\$|\\\])\s*$", stripped):
                in_math = False
            continue
        opened = _FENCE_RE.match(stripped)
        if opened:
            fence = opened.group(1)
            lines.append(line)
            continue
        if stripped in ("$$", "\\[") or (
            stripped.startswith("$$") and not re.search(r"\$\$.*\$\$", stripped)
        ):
            in_math = True
            lines.append(line)
            continue
        if stripped:
            lines.append(line)
        else:
            flush()
    flush()
    return blocks


def _kind(text: str) -> str:
    t = text.lstrip()
    if _FENCE_RE.match(t):
        return "code"
    if re.match(r"(\$\$|\\\[|\\begin\{)", t):
        return "math"
    if re.match(r"#{1,6}\s", t):
        return "heading"
    lines = [line.strip() for line in t.split("\n")]
    if all(_IMAGE_LINE_RE.match(line) for line in lines):
        return "image"
    # Images with their caption in one block.
    if _IMAGE_LINE_RE.match(lines[0]):
        images = [line for line in lines if _IMAGE_LINE_RE.match(line)]
        if lines[: len(images)] == images and _is_caption(
            " ".join(lines[len(images) :])
        ):
            return "figure"
    if t.startswith("|"):
        return "table"
    if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", t):
        return "rule"
    if re.match(r"\[\^[^\]\s]+\]:", t):
        return "note"
    if re.match(r"([-+*]|\d{1,3}[.)])\s", t):
        return "list"
    if re.match(r"( {4}|\t|>|<)", text):
        return "other"
    return "paragraph"


# -- rules ----------------------------------------------------------------------

# "… end." / "… end.”" / "… $x = 3.$"
_SENTENCE_END_RE = re.compile(r"[.!?](?:[\"'”’)\]*_]+|\s*\$|\s*\\\))?\s*$")
# Note references after the full stop: "… end.[^3]", "… end.¹", "… end.$^{1}$".
_TRAILING_REF_RE = re.compile(
    r"(?:\[\^[^\]\s]+\]|\^\{[^{}]*\}|<sup>[^<]*</sup>|[⁰¹²³⁴⁵⁶⁷⁸⁹]"
    r"|\\\(\s*(?:\{\s*\})?\s*\^[^)]*?\\\)|\$\s*(?:\{\s*\})?\s*\^[^$]*\$|\s)+$"
)
_ABBREVIATION_END_RE = re.compile(
    r"\b(?:al|e\.g|i\.e|etc|vs|cf|Fig|Figs|Eq|Eqs|Sec|Secs|Tab|App|resp|approx|No)\.\s*$"
)
_PAGE_NUMBER_RE = re.compile(r"\d{1,4}")
_CAPTION_RE = re.compile(
    r"(?:\*\*|\*|_)?(?:Supplementary |Extended Data )?"
    r"(?:Figure|Fig\.|Table|Algorithm|Listing|Transcript)\s*[A-Z]?\d+[a-z]?"
    # "Figure 3: …", "Fig. 3 | …", "Table 1 List of …" — not "Figure 3 shows …".
    r"(?:\*\*|\*|_)?(?:\s*[.:|]|\s+[A-Z(]|\s*$)"
)
# The first half must be real prose, not a short label.
_MIN_HEAD_CHARS = 40
# Past a figure, a short lowercase line may be text OCR'd out of the figure.
_MIN_TAIL_AFTER_FIGURE_CHARS = 40
# bioRxiv's running notice is ~370 characters.
_RUNNING_HEADER_MAX_CHARS = 500
_RUNNING_HEADER_MIN_COUNT = 3
_EQUATION_NUMBER_END_RE = re.compile(r"\(\d{1,3}[a-z]?\)$")
_FLOAT_KINDS = ("image", "figure", "table", "note")
# A footnote marker at the start: `\( ^{6} \)`, `$^{6}$`, `${}^{6}$`, `^{6}`,
# `<sup>6</sup>`, `⁶`.
_LEAD_MARKER_RE = re.compile(
    r"(?:\\\(\s*(?:\{\s*\})?\s*\^|\$\s*(?:\{\s*\})?\s*\^|\^\{|<sup>|[⁰¹²³⁴⁵⁶⁷⁸⁹])"
)
_INLINE_MATH_RE = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)", re.S)
_MIN_FORMULA_PROSE_LETTERS = 15
_CODE_WINDOW = 100
_CODE_RE = re.compile(
    r"[{}=]|\)\s*:|^\s*(?:def|class|import|from\s+\S+\s+import)\s", re.M
)
# `data^{8}`: a note marker outside math, not code.
_MARKER_BRACES_RE = re.compile(r"\^\{[^{}]*\}")
# A word, possibly hyphenated, or an abbreviation ("e.g.,"), with trailing
# punctuation — not a domain name ("blueprint-epigenome.eu.").
_WORD_TOKEN_RE = re.compile(
    r"(?:[^\W\d_]+(?:[-'’/—–][^\W\d_]+)*|(?:[^\W\d_]\.){1,3})[.,;:!?)\]”’\"']*"
)
_URL_START_RE = re.compile(r"(?:https?:|www\.)")
_HYPHEN_END_RE = re.compile(r"([^\W\d_]+)-$")
_WORD_START_RE = re.compile(r"[^\W\d_]+")


def _ends_sentence(text: str) -> bool:
    text = _TRAILING_REF_RE.sub("", text.rstrip())
    return bool(_SENTENCE_END_RE.search(text)) and not _ABBREVIATION_END_RE.search(text)


def _is_caption(text: str) -> bool:
    return bool(_CAPTION_RE.match(text.strip()))


def _starts_prose(text: str) -> bool:
    """Starts like prose: not a footnote of an old ingest (marker first), a
    code fragment, a transcript line ("[tool] …") or a label."""
    text = text.lstrip()
    if _LEAD_MARKER_RE.match(text):
        return False
    return text[:1].isalpha() or text[:1] in "*_(\"“'\\$"


def _is_formula(text: str) -> bool:
    """A paragraph that is (nearly) all inline math: a display equation."""
    prose = _INLINE_MATH_RE.sub("", text)
    return sum(c.isalpha() for c in prose) < _MIN_FORMULA_PROSE_LETTERS


def _continues(text: str) -> bool:
    """Reads like the rest of a sentence: its first word is a lowercase word
    — not an identifier (`send_message`), a call or a bare URL (a footnote
    of an old ingest)."""
    text = text.lstrip()
    first = text.split(None, 1)[0] if text else ""
    return (
        first[:1].isalpha()
        and first[0].islower()
        and not _URL_START_RE.match(text)
        and bool(_WORD_TOKEN_RE.fullmatch(first))
    )


def _glue(head: str, tail: str, text: str) -> str:
    """`head` + `tail`, removing a hyphen that broke a word over the gap."""
    head = head.rstrip()
    tail = tail.lstrip()
    broken = _HYPHEN_END_RE.search(head)
    if not broken:
        return f"{head} {tail}"
    first = _WORD_START_RE.match(tail)
    rest = first.group(0) if first else ""
    hyphenated = f"{broken.group(1)}-{rest}"
    # The paper writes the compound with its hyphen elsewhere: keep it.
    if rest and re.search(rf"(?<![^\W\d_]){re.escape(hyphenated)}", text, re.I):
        return f"{head}{tail}"
    return f"{head[:-1]}{tail}"


def _caption(block: _Block) -> str | None:
    """The caption a gap block carries (a caption paragraph, or the caption
    under a figure's images)."""
    if block.kind == "paragraph" and _is_caption(block.text):
        return block.text.strip()
    if block.kind == "figure":
        lines = block.text.strip().split("\n")
        return " ".join(line for line in lines if not _IMAGE_LINE_RE.match(line))
    return None


def _title_like(text: str) -> bool:
    """A short Title Case line — a running title or a label, not prose."""
    if "\n" in text or len(text) > 120 or re.search(r"[.,;:]", text):
        return False
    words = re.findall(r"[^\W\d_]{4,}", text)
    return bool(words) and sum(w[0].isupper() for w in words) * 3 >= len(words) * 2


def _looks_like_code(text: str, *, end: bool) -> bool:
    """Code OCR'd as a paragraph (`… } for (int i = 0; …`) at the paragraph's
    end (a head) or start (a tail): prose outside inline math has no braces,
    `=` or `):` there."""
    prose = _MARKER_BRACES_RE.sub("", _INLINE_MATH_RE.sub("", text)).strip()
    lines = prose.split("\n")
    if (lines[-1] if end else lines[0]).rstrip().endswith(";"):
        return True
    window = prose[-_CODE_WINDOW:] if end else prose[:_CODE_WINDOW]
    return bool(_CODE_RE.search(window))


def _open_ended(text: str) -> bool:
    """A prose paragraph that stops mid-sentence."""
    text = text.strip()
    return (
        len(text) >= _MIN_HEAD_CHARS
        and _starts_prose(text)
        and not _URL_START_RE.match(text)
        and not _is_caption(text)
        and not _title_like(text)
        and not _is_formula(text)
        and not _looks_like_code(text, end=True)
        and not _ends_sentence(text)
        # "… is given by:" introduces a display or a list.
        and not text.endswith(":")
        # An equation with its number: what follows ("where …") is a new
        # paragraph under it.
        and not _EQUATION_NUMBER_END_RE.search(text)
    )


def rejoin(markdown: str) -> str:
    """Join paragraphs split mid-sentence by a page/column break (see module
    docstring). Returns `markdown` itself when nothing was joined."""
    if not markdown:
        return markdown
    blocks = _split_blocks(markdown)
    counts = Counter(b.text.strip() for b in blocks if b.kind == "paragraph")

    def furniture(b: _Block) -> bool:
        """Page furniture: dropped from a gap that gets joined over."""
        text = b.text.strip()
        if b.kind != "paragraph" or "\n" in text:
            return False
        return bool(_PAGE_NUMBER_RE.fullmatch(text)) or (
            len(text) <= _RUNNING_HEADER_MAX_CHARS
            and counts[text] >= _RUNNING_HEADER_MIN_COUNT
        )

    def floating(b: _Block) -> bool:
        """A float: moved to after the joined paragraph."""
        return b.kind in _FLOAT_KINDS or _caption(b) is not None

    out: list[str] = []
    i = 0
    changed = False
    while i < len(blocks):
        block = blocks[i]
        i += 1
        text = block.text
        if block.kind != "paragraph" or furniture(block):
            out.append(text)
            continue
        moved: list[str] = []
        while _open_ended(text):
            j = i
            while j < len(blocks) and (furniture(blocks[j]) or floating(blocks[j])):
                j += 1
            if j >= len(blocks):
                break
            gap, nxt = blocks[i:j], blocks[j]
            if nxt.kind != "paragraph" or not _continues(nxt.text):
                break
            if _looks_like_code(nxt.text, end=False):
                break
            captions = [c for c in map(_caption, gap) if c is not None]
            # A caption cut off in the gap: the lowercase text may be its rest.
            if captions and not _ends_sentence(captions[-1]):
                break
            # Past a figure, a short lowercase line may be the figure's own
            # text ("loss", "steps") — unless it ends the sentence.
            if (
                any(b.kind in ("image", "figure") for b in gap)
                and len(nxt.text.strip()) < _MIN_TAIL_AFTER_FIGURE_CHARS
                and not _ends_sentence(nxt.text)
            ):
                break
            text = _glue(text, nxt.text, markdown)
            moved.extend(b.text for b in gap if floating(b))
            i = j + 1
            changed = True
        out.append(text)
        out.extend(moved)
    return "\n\n".join(out) if changed else markdown
