"""
Best-effort markdown→pymupdf normalizer used as the cheap first pass in
citation reconciliation.

When the agentic chat finishes streaming, every quoted citation is grounded
in Mistral OCR markdown — but the PDF highlighter searches against pymupdf
text. Markdown emphasis, LaTeX commands, and table syntax mean the two
representations don't usually match verbatim. This module strips the
markdown/LaTeX layer so the result resembles what pymupdf would produce.

Empirically (8755 candidate quotes synthesized from 14 backfilled papers):
~83% overall match rate — ~82% on prose, ~86% on headings, ~89% on
in-sentence sub-spans, ~40% on math-heavy content, ~61% on tables. The LLM
reconciliation pass picks up what we miss here.
"""

import html
import re
import unicodedata
from typing import Optional

_LATEX_UNICODE = {
    # Greek (lowercase + common uppercase)
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ",
    "Lambda": "Λ", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # Common operators & relations
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
}


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
# LaTeX escapes punctuation that's syntactically reserved (% & $ # _ { } etc.)
# with a backslash. pymupdf has the literal char, so drop the backslash.
_BACKSLASH_PUNCT = re.compile(r"\\([%$&#_{}\[\]~^])")
_LATEX_BRACES = re.compile(r"\{([^{}]*)\}")
_HEADING_PREFIX = re.compile(
    r"^\s*#{1,6}\s+(?:\d+(?:\.\d+)*\s+)?", re.MULTILINE
)
_BULLET_PREFIX = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_PIPE = re.compile(r"\s*\|\s*")
_TABLE_SEP_ROW = re.compile(r"^\s*[-:|\s]+\s*$", re.MULTILINE)
# Closing tags carry no content, so it's always safe to strip them — even
# unbalanced ones (Mistral OCR sometimes leaks stray `</tag>` from prompt
# text into prose). Opening tags are stripped only for well-known formatting
# tags so that custom-named tags like `<answer>X</answer>` (which some PDFs
# embed literally) are preserved on the needle to align with pymupdf.
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

# Curly / typographic punctuation → ASCII. Applied symmetrically to both the
# OCR-markdown needle and pymupdf haystack so a `'` in one side matches a `'`
# in the other.
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
    "–": "-", "—": "-", "−": "-",
    "­": "",  # soft hyphen
    "​": "", "‌": "", "‍": "",  # zero-width spaces
    "﻿": "",  # BOM / zero-width no-break space
    # Bullet / dot variants — pymupdf and Mistral OCR don't agree on which
    # codepoint to use; fold all to one form so bullet lists align.
    "·": "•", "∙": "•", "⋅": "•",
    # Ligatures that pymupdf sometimes emits separately.
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
})


def _canonicalize(s: str) -> str:
    """HTML-decode + Unicode NFC + punctuation fold. Idempotent on ASCII."""
    s = html.unescape(s)
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_PUNCT_FOLD)
    return s


def normalize_for_match(s: str) -> str:
    """Strip markdown / LaTeX so the output resembles pymupdf plain-text."""
    if not s:
        return ""

    s = _IMG.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _FOOTNOTE_REF.sub("", s)
    s = _FENCE.sub("", s)
    s = _HTML_CLOSE_TAG.sub("", s)
    s = _HTML_OPEN_TAG.sub("", s)

    # Math: drop $ delimiters first, then unwind LaTeX in priority order.
    s = _INLINE_MATH.sub(lambda m: m.group(1), s)
    s = _INLINE_MATH.sub(lambda m: m.group(1), s)

    s = _LATEX_FRAC.sub(lambda m: f"{m.group(1)}/{m.group(2)}", s)
    # Wrap typeface contents with spaces so adjacent commands like
    # `\in\mathbb{R}` don't fuse to `\inR` and mask the next \-command.
    s = _LATEX_TYPEFACE_WRAPPER.sub(lambda m: f" {m.group(1)} ", s)
    s = _LATEX_CMD.sub(lambda m: f" {m.group(1)} ", s)
    s = _BACKSLASH_LETTER.sub(
        lambda m: _LATEX_UNICODE.get(m.group(1), m.group(1)), s
    )
    # Drop backslash escapes for punctuation: `\%` → `%`, `\$` → `$`, etc.
    s = _BACKSLASH_PUNCT.sub(lambda m: m.group(1), s)
    s = _LATEX_SUBSCRIPT.sub(lambda m: m.group(1), s)
    s = _LATEX_SUPERSCRIPT.sub(lambda m: m.group(1), s)
    s = _LATEX_BRACES.sub(lambda m: m.group(1), s)

    s = _HEADING_PREFIX.sub("", s)
    s = _BULLET_PREFIX.sub("", s)
    s = _TABLE_SEP_ROW.sub("", s)
    s = _TABLE_PIPE.sub(" ", s)

    s = _BOLD_OR_ITALIC.sub(lambda m: m.group(1), s)
    # Orphan emphasis markers — stars left over when a sub-span quote starts
    # or ends mid-`*...*`. Stripped here AND in `normalize_pdf_text` so both
    # sides realign (pymupdf preserves footnote stars in author lists).
    s = s.replace("*", "")

    s = _canonicalize(s)
    s = _MULTI_WS.sub(" ", s).strip()
    return s


def normalize_pdf_text(s: str) -> str:
    """Whitespace collapse to mirror what the highlighter sees on the page."""
    s = _canonicalize(s or "")
    # Strip the same HTML closing tags we strip from the needle so that
    # `<answer>X</answer>` in OCR markdown aligns with the literal occurrence
    # in pymupdf when the PDF embeds the angle brackets verbatim.
    s = _HTML_CLOSE_TAG.sub("", s)
    s = s.replace("*", "")
    return _MULTI_WS.sub(" ", s).strip()


_DEHYPHEN_KEEP = re.compile(r"(\w)-\s+(\w)")  # `Llama-\nInstruct` → `Llama-Instruct`
_DEHYPHEN_DROP = re.compile(r"(\w)-\s+(\w)")  # same regex, drop the hyphen


def find_in_pdf_text(quote: str, page_text: str) -> Optional[str]:
    """Try to find the normalized quote inside the page's pymupdf text.

    Returns the matched form (suitable for the PDF highlighter) or None.
    The highlighter is whitespace-tolerant, so the whitespace-collapsed form
    is a reliable search target — pdf.js will find it on the page even when
    the source text has line breaks mid-sentence.

    Handles end-of-line hyphenation: pymupdf preserves `\\n` after a hyphen
    at the line wrap, which whitespace-collapses to `- ` (hyphen + space).
    The OCR quote has the original token (`Llama-3.3-70B-Instruct`), so we
    try the haystack with `- ` rejoined — keeping the hyphen for compound
    names, dropping it for soft-hyphenation cases.
    """
    if not quote or not page_text:
        return None
    needle = normalize_for_match(quote)
    if len(needle) < 4:
        return None
    haystack = normalize_pdf_text(page_text)
    if needle in haystack:
        return needle

    # Rejoin `<word>- <word>` → `<word>-<word>` (keep hyphen).
    rejoined_keep = _DEHYPHEN_KEEP.sub(r"\1-\2", haystack)
    if rejoined_keep != haystack and needle in rejoined_keep:
        return needle

    # Rejoin `<word>- <word>` → `<word><word>` (drop hyphen, soft break).
    rejoined_drop = _DEHYPHEN_DROP.sub(r"\1\2", haystack)
    if rejoined_drop != haystack and needle in rejoined_drop:
        return needle

    # Section-number period fallback. Mistral OCR writes section headings
    # as `1 Introduction` / `2 Setting and methodology`, while pymupdf
    # typically extracts `1. Introduction` / `2. Setting and methodology`.
    # Try the needle with a period inserted after a top-level section number.
    sn = _SECTION_NUM_PREFIX.match(needle)
    if sn and "." not in sn.group(1):
        with_period = f"{sn.group(1)}. {needle[sn.end():]}"
        if with_period in haystack:
            return with_period

    # Whitespace-insensitive fallback. Catches LaTeX-induced spacing
    # divergence: markdown `\mathrm{VFT}_{\mathrm{RL}}` normalizes to
    # `VFT RL`, but pymupdf renders it as `VFTRL` (no space).
    if len(needle) >= 12:
        match = _compact_find(needle, haystack, _is_ws)
        if match is not None:
            return match

    # Punctuation+whitespace-insensitive fallback. Catches mid-quote
    # divergence in a narrow set of chars (`.,:;-+{}` plus geometric
    # bullets) — `[CoT, 34]` vs `[CoT; 34]`, `4 Results` vs `4. Results`,
    # `Conformity` vs `- Conformity`. Length floor of 30 chars keeps FP risk
    # low: counterfactual swap+inject tests find zero matches at this floor.
    if len(needle) >= 30:
        match = _compact_find(needle, haystack, _is_ws_or_punct)
        if match is not None:
            return match

    return None


_SECTION_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)\s+(?=\S)")
_PUNCT_NOISE = set(".,:;-+{}■●▲○◇◆□△▪▫")


def _is_ws(ch: str) -> bool:
    return ch.isspace()


def _is_ws_or_punct(ch: str) -> bool:
    return ch.isspace() or ch in _PUNCT_NOISE


def _compact_find(needle: str, haystack: str, drop_pred) -> Optional[str]:
    """Substring search ignoring chars matched by `drop_pred`.

    Returns the original-haystack substring spanning the match, or None. The
    returned span comes straight from the haystack so the PDF highlighter
    can find it on the page even though the needle had different punctuation
    or spacing.
    """
    needle_compact = "".join(c for c in needle if not drop_pred(c))
    if len(needle_compact) < 8:
        return None
    pos_map: list[int] = []
    haystack_compact_chars: list[str] = []
    for i, ch in enumerate(haystack):
        if not drop_pred(ch):
            pos_map.append(i)
            haystack_compact_chars.append(ch)
    haystack_compact = "".join(haystack_compact_chars)
    idx = haystack_compact.find(needle_compact)
    if idx < 0:
        return None
    start = pos_map[idx]
    end = pos_map[idx + len(needle_compact) - 1] + 1
    return haystack[start:end]
