"""
Best-effort markdown→pymupdf normalizer used as the cheap first pass in
citation reconciliation.

When the agentic chat finishes streaming, every quoted citation is grounded
in Mistral OCR markdown — but the PDF highlighter searches against pymupdf
text. Markdown emphasis, LaTeX commands, and table syntax mean the two
representations don't usually match verbatim. This module strips the
markdown/LaTeX layer so the result resembles what pymupdf would produce.

Empirically (see scripts/test_citation_normalizer.py): ~88% match rate on
prose and headings, ~25-44% on math-heavy or table content. The LLM
reconciliation pass picks up what we miss here.
"""

import re
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
}


_BOLD_OR_ITALIC = re.compile(r"\*{1,3}([^*]+?)\*{1,3}")
_INLINE_MATH = re.compile(r"\${1,2}([^$]+?)\${1,2}")
_LATEX_FRAC = re.compile(r"\\frac\{([^}]*)\}\{([^}]*)\}")
_LATEX_TYPEFACE_WRAPPER = re.compile(
    r"\\(?:mathbb|mathcal|mathfrak|mathbf|mathit|mathsf|mathtt|mathrm|"
    r"operatorname|text|boldsymbol|pmb)\{([^}]*)\}"
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
_HTML_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"```[a-zA-Z]*")
_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_IMG = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_MULTI_WS = re.compile(r"\s+")


def normalize_for_match(s: str) -> str:
    """Strip markdown / LaTeX so the output resembles pymupdf plain-text."""
    if not s:
        return ""

    s = _IMG.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _FOOTNOTE_REF.sub("", s)
    s = _FENCE.sub("", s)
    s = _HTML_TAG.sub("", s)

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

    s = _MULTI_WS.sub(" ", s).strip()
    return s


def normalize_pdf_text(s: str) -> str:
    """Whitespace collapse to mirror what the highlighter sees on the page."""
    return _MULTI_WS.sub(" ", s or "").strip()


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

    return None
