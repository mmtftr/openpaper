"""Anchor quoted text (AI highlights) to rects in the PDF's text layer.

Port of `jobs/src/highlight_anchor.py`. The quote comes from the OCR markdown
(LaTeX, markdown emphasis, HTML tags, curly quotes), the PDF text layer has
ligature glyphs, hyphenated line breaks and hard line breaks, so both sides are
normalised before matching, with the same fallbacks as the old code:

1. exact (case-insensitive) match of the normalised quote;
2. a word split by a line-end hyphen, rejoined with / without the hyphen;
3. "3 Results" in the quote vs "3. Results" in the PDF;
4. whitespace-insensitive (quotes of 12+ chars), then whitespace- and
   punctuation-insensitive (30+ chars).

Difference from the old code: it found the matching *string* and then asked
pymupdf's `search_for` for its rects, which fails when the PDF uses ligature
glyphs ("ﬁ") or the match was dehyphenated. Here every normalised character
keeps the box of the PDF character it came from, so a match gives its rects
directly (one rect per line), whatever normalisation it went through.

Coordinates: pymupdf's text boxes are top-left origin, y down, in page
points — exactly react-pdf-highlighter-extended's `Scaled` convention with
the page size in `width`/`height`. `usePdfCoordinates` is deliberately NOT
set: it expects PDF-native bottom-left coordinates and would flip every
highlight vertically (see `app.schemas.highlight`).
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional

import pymupdf

# -- normalisation (unchanged from the jobs service) ---------------------------

_LATEX_UNICODE = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "in": "∈", "notin": "∉", "subset": "⊂", "supset": "⊃", "cup": "∪",
    "cap": "∩", "leq": "≤", "geq": "≥", "neq": "≠", "approx": "≈",
    "sim": "∼", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃",
    "times": "×", "cdot": "·", "pm": "±", "mp": "∓", "sum": "∑", "prod": "∏",
    "int": "∫", "sqrt": "√",
    "dots": "…", "ldots": "…", "cdots": "…", "vdots": "…", "ddots": "…",
    "ll": "≪", "gg": "≫", "equiv": "≡", "propto": "∝", "perp": "⊥",
    "circ": "∘", "star": "⋆", "bullet": "•", "oplus": "⊕", "otimes": "⊗",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "leftrightarrow": "↔", "mapsto": "↦", "land": "∧", "lor": "∨", "neg": "¬",
}  # fmt: skip

_BOLD_OR_ITALIC = re.compile(r"\*{1,3}([^*]+?)\*{1,3}")
_INLINE_MATH = re.compile(r"\${1,2}([^$]+?)\${1,2}")
_LATEX_FRAC = re.compile(r"\\frac\{([^}]*)\}\{([^}]*)\}")
_LATEX_TYPEFACE_WRAPPER = re.compile(
    r"\\(?:mathbb|mathcal|mathfrak|mathbf|mathit|mathsf|mathtt|mathrm|"
    r"operatorname|text|boldsymbol|pmb|bm|widehat|widetilde|overline|"
    r"underline|bar|hat|tilde|vec|dot|ddot|check|breve|acute|grave)\{([^}]*)\}"
)
_LATEX_CMD = re.compile(r"\\(?:mathrm|operatorname|text)\{([^}]*)\}")
_LATEX_SUBSCRIPT = re.compile(r"_\{([^}]*)\}")
_LATEX_SUPERSCRIPT = re.compile(r"\^\{([^}]*)\}")
_BACKSLASH_LETTER = re.compile(r"\\([a-zA-Z]+)(?![a-zA-Z])")
_BACKSLASH_PUNCT = re.compile(r"\\([%$&#_{}\[\]~^])")
_LATEX_BRACES = re.compile(r"\{([^{}]*)\}")
_HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s+(?:\d+(?:\.\d+)*\s+)?", re.MULTILINE)
_BULLET_PREFIX = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_PIPE = re.compile(r"\s*\|\s*")
_TABLE_SEP_ROW = re.compile(r"^\s*[-:|\s]+\s*$", re.MULTILINE)
_HTML_CLOSE_TAG = re.compile(r"</[a-zA-Z][^>]*>")
_HTML_OPEN_TAG = re.compile(
    r"<(?:i|b|u|s|em|strong|sub|sup|br|hr|p|div|span|small|big|tt|"
    r"code|pre|blockquote|kbd|var|cite|mark|ins|del|abbr|acronym|"
    r"strike|font)\b[^>]*>",
    re.IGNORECASE,
)
_FENCE = re.compile(r"```[a-zA-Z]*")
_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_IMG = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_MULTI_WS = re.compile(r"\s+")
_DEHYPHEN = re.compile(r"(\w)-\s+(\w)")
_SECTION_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)\s+(?=\S)")
_PUNCT_NOISE = set(".,:;-+{}■●▲○◇◆□△▪▫")
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    "–": "-", "—": "-", "−": "-",
    "\u00ad": "",
    "\u200b": "", "\u200c": "", "\u200d": "",
    "\ufeff": "",
    "·": "•", "∙": "•", "⋅": "•",
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
})  # fmt: skip

# Shortest normalised quote worth anchoring, and the lengths from which the
# looser whitespace- / punctuation-insensitive matches are allowed.
_MIN_QUOTE = 4
_MIN_COMPACT = 12
_MIN_PUNCT_COMPACT = 30
_MIN_COMPACT_CHARS = 8


def _canonicalize(text: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(text)).translate(_PUNCT_FOLD)


def normalize_quote(text: str) -> str:
    """The quote (OCR markdown) as plain text, comparable with PDF text."""
    if not text:
        return ""

    text = _IMG.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _FOOTNOTE_REF.sub("", text)
    text = _FENCE.sub("", text)
    text = _HTML_CLOSE_TAG.sub("", text)
    text = _HTML_OPEN_TAG.sub("", text)

    text = _INLINE_MATH.sub(lambda m: m.group(1), text)
    text = _INLINE_MATH.sub(lambda m: m.group(1), text)
    text = _LATEX_FRAC.sub(lambda m: f"{m.group(1)}/{m.group(2)}", text)
    text = _LATEX_TYPEFACE_WRAPPER.sub(lambda m: f" {m.group(1)} ", text)
    text = _LATEX_CMD.sub(lambda m: f" {m.group(1)} ", text)
    text = _BACKSLASH_LETTER.sub(
        lambda m: _LATEX_UNICODE.get(m.group(1), m.group(1)), text
    )
    text = _BACKSLASH_PUNCT.sub(lambda m: m.group(1), text)
    text = _LATEX_SUBSCRIPT.sub(lambda m: m.group(1), text)
    text = _LATEX_SUPERSCRIPT.sub(lambda m: m.group(1), text)
    text = _LATEX_BRACES.sub(lambda m: m.group(1), text)

    text = _HEADING_PREFIX.sub("", text)
    text = _BULLET_PREFIX.sub("", text)
    text = _TABLE_SEP_ROW.sub("", text)
    text = _TABLE_PIPE.sub(" ", text)
    text = _BOLD_OR_ITALIC.sub(lambda m: m.group(1), text)
    text = text.replace("*", "")

    return _MULTI_WS.sub(" ", _canonicalize(text)).strip()


# -- the page's text, one normalised char per entry, each with its box ----------

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 (top-left origin)


@dataclass
class PageChars:
    """A page's text normalised like `_normalize_pdf_text` did (canonical
    punctuation, ligatures expanded, no "*", whitespace collapsed to one
    space), keeping for every char the PDF box and line it came from."""

    page_number: int  # 1-based
    width: float
    height: float
    text: str  # the normalised text, lowercased char by char
    boxes: list[Optional[Box]]  # None for the spaces we inserted
    lines: list[int]  # index of the PDF line each char came from


def _lower(char: str) -> str:
    lowered = char.lower()
    return lowered if len(lowered) == 1 else char


def page_chars(page: Any, page_number: int) -> PageChars:
    chars: list[str] = []
    boxes: list[Optional[Box]] = []
    lines: list[int] = []

    def push(char: str, box: Optional[Box], line: int) -> None:
        if char.isspace():
            # Collapse runs of whitespace; never start with one.
            if not chars or chars[-1] == " ":
                return
            chars.append(" ")
            boxes.append(None)
            lines.append(line)
            return
        chars.append(_lower(char))
        boxes.append(box)
        lines.append(line)

    line_index = 0
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    box = tuple(float(v) for v in char["bbox"])
                    # A soft hyphen in the text layer is a line-end hyphen
                    # (PNAS & co.): keep it as "-" so the dehyphenating
                    # matches rejoin the word (folding drops it otherwise).
                    c = "-" if char["c"] == "\u00ad" else char["c"]
                    for folded in _canonicalize(c):
                        if folded != "*":
                            push(folded, box, line_index)  # type: ignore[arg-type]
            push(" ", None, line_index)  # line break
            line_index += 1

    while chars and chars[-1] == " ":
        chars.pop()
        boxes.pop()
        lines.pop()
    return PageChars(
        page_number=page_number,
        width=float(page.rect.width),
        height=float(page.rect.height),
        text="".join(chars),
        boxes=boxes,
        lines=lines,
    )


# -- matching -------------------------------------------------------------------


def _find(
    needle: str, page: PageChars, keep: Callable[[int], bool]
) -> Optional[tuple[int, int]]:
    """Find `needle` in the page text restricted to the chars `keep` accepts;
    return the (first, last) char index of the match in the full text."""
    positions = [i for i in range(len(page.text)) if keep(i)]
    at = "".join(page.text[i] for i in positions).find(needle)
    if at < 0:
        return None
    return positions[at], positions[at + len(needle) - 1]


def _hyphen_breaks(text: str, drop_hyphen: bool) -> set[int]:
    """Indexes to drop to rejoin words split by "-" + whitespace."""
    dropped: set[int] = set()
    for match in _DEHYPHEN.finditer(text):
        hyphen = match.start() + 1
        dropped.update(range(hyphen + 1, match.end() - 1))  # the whitespace
        if drop_hyphen:
            dropped.add(hyphen)
    return dropped


def find_quote(quote: str, page: PageChars) -> Optional[tuple[int, int]]:
    """(first, last) char index of `quote` on `page`, or None."""
    needle = "".join(_lower(c) for c in normalize_quote(quote))
    if len(needle) < _MIN_QUOTE:
        return None

    def everything(_: int) -> bool:
        return True

    attempts: list[tuple[str, Callable[[int], bool]]] = [(needle, everything)]
    for drop_hyphen in (False, True):
        dropped = _hyphen_breaks(page.text, drop_hyphen)
        if dropped:
            attempts.append((needle, lambda i, d=dropped: i not in d))

    section = _SECTION_NUM_PREFIX.match(needle)
    if section and "." not in section.group(1):
        attempts.append((f"{section.group(1)}. {needle[section.end() :]}", everything))

    # Looser: ignore whitespace, then whitespace and punctuation noise, on
    # both sides.
    for min_len, ignore in ((_MIN_COMPACT, _is_space), (_MIN_PUNCT_COMPACT, _is_noise)):
        if len(needle) < min_len:
            continue
        compact = "".join(c for c in needle if not ignore(c))
        if len(compact) >= _MIN_COMPACT_CHARS:
            attempts.append((compact, lambda i, f=ignore: not f(page.text[i])))

    for text, keep in attempts:
        span = _find(text, page, keep)
        if span is not None:
            return span
    return None


def _is_space(char: str) -> bool:
    return char.isspace()


def _is_noise(char: str) -> bool:
    return char.isspace() or char in _PUNCT_NOISE


# -- rects ----------------------------------------------------------------------


def _scaled(box: Box, page: PageChars) -> dict[str, float | int]:
    return {
        "x1": box[0],
        "y1": box[1],
        "x2": box[2],
        "y2": box[3],
        "width": page.width,
        "height": page.height,
        "pageNumber": page.page_number,
    }


def scaled_position(page: PageChars, first: int, last: int) -> Optional[dict[str, Any]]:
    """The `ScaledPosition` JSON for chars first..last: one rect per line."""
    per_line: dict[int, list[float]] = {}
    for i in range(first, last + 1):
        box = page.boxes[i]
        if box is None:
            continue
        rect = per_line.get(page.lines[i])
        if rect is None:
            per_line[page.lines[i]] = list(box)
        else:
            rect[0], rect[1] = min(rect[0], box[0]), min(rect[1], box[1])
            rect[2], rect[3] = max(rect[2], box[2]), max(rect[3], box[3])
    if not per_line:
        return None
    rects = [_scaled(tuple(r), page) for r in per_line.values()]  # type: ignore[arg-type]
    bounding = _scaled(
        (
            min(r[0] for r in per_line.values()),
            min(r[1] for r in per_line.values()),
            max(r[2] for r in per_line.values()),
            max(r[3] for r in per_line.values()),
        ),
        page,
    )
    return {"boundingRect": bounding, "rects": rects}


# -- entry point ------------------------------------------------------------------


def anchor_quotes(pdf: bytes, quotes: list[str]) -> list[Optional[dict[str, Any]]]:
    """Anchor each quote on the first page it's found on.

    Returns, per quote, `{"page_number": n, "position": ScaledPosition JSON}`
    or None when it wasn't found. Top-level and picklable, so the worker can
    run it in its process pool.
    """
    anchors: list[Optional[dict[str, Any]]] = [None] * len(quotes)
    if not quotes:
        return anchors
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pages = [page_chars(doc[i], i + 1) for i in range(doc.page_count)]
    for index, quote in enumerate(quotes):
        for page in pages:
            span = find_quote(quote, page)
            position = scaled_position(page, *span) if span else None
            if position is not None:
                anchors[index] = {
                    "page_number": page.page_number,
                    "position": position,
                }
                break
    return anchors
