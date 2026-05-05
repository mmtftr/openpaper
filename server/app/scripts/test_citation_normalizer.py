"""
Empirical test: do normalized Mistral-markdown snippets match pymupdf text?

Pulls the OCR pages from a backfilled paper, picks lines that *look like*
candidate citations (sentences with bold/italic/math/table syntax), runs a
candidate normalizer, and checks whether each normalized snippet appears as
a substring in the same PDF's pymupdf-extracted plain text.

Run inside the server container:
    docker compose exec server python -m app.scripts.test_citation_normalizer
"""

import io
import logging
import os
import re
import sys
from collections import Counter
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from app.database.database import SessionLocal
from app.database.models import Paper
from app.helpers.s3 import s3_service

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Candidate normalizer (option 1)
# ----------------------------------------------------------------------


_BOLD_OR_ITALIC = re.compile(r"\*{1,3}([^*]+?)\*{1,3}")
_INLINE_MATH = re.compile(r"\${1,2}([^$]+?)\${1,2}")
_LATEX_CMD = re.compile(r"\\(?:mathrm|mathbf|mathit|operatorname|text|mathcal|boldsymbol)\{([^}]*)\}")
_LATEX_FRAC = re.compile(r"\\frac\{([^}]*)\}\{([^}]*)\}")
_LATEX_SUBSCRIPT = re.compile(r"_\{([^}]*)\}")
_LATEX_SUPERSCRIPT = re.compile(r"\^\{([^}]*)\}")
# LaTeX command → unicode glyph the PDF actually contains. Covers the common
# Greek letters and math operators we see in the misses.
_LATEX_UNICODE: dict = {
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
    # blackboard / common typeface markers — drop the marker, keep what was inside
}
# \mathbb{R} / \mathcal{X} / \mathbf{x} → R / X / x. Covers the wrappers the
# PDF rendering flattens to a single styled glyph (which pymupdf returns as
# the plain letter or its unicode-styled variant; either way the LaTeX
# wrapper isn't in the text).
_LATEX_TYPEFACE_WRAPPER = re.compile(
    r"\\(?:mathbb|mathcal|mathfrak|mathbf|mathit|mathsf|mathtt|mathrm|"
    r"operatorname|text|boldsymbol|pmb)\{([^}]*)\}"
)
# Lookahead requires a non-letter so `\theta_{l}` correctly captures `theta`
# even though `_` is a word char (which `\b` would treat as a continuation).
_BACKSLASH_LETTER = re.compile(r"\\([a-zA-Z]+)(?![a-zA-Z])")
_HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s+(?:\d+(?:\.\d+)*\s+)?", re.MULTILINE)
_BULLET_PREFIX = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_TABLE_PIPE = re.compile(r"\s*\|\s*")
_TABLE_SEP_ROW = re.compile(r"^\s*[-:|\s]+\s*$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>]+>")
_FENCE = re.compile(r"```[a-zA-Z]*")
_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_IMG = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_LATEX_BRACES = re.compile(r"\{([^{}]*)\}")
_MULTI_WS = re.compile(r"\s+")


def normalize_for_match(s: str) -> str:
    """Strip markdown/LaTeX so the result resembles what pymupdf would yield.

    Conservative: keeps content verbatim where possible, just drops the syntax
    that won't appear in a PDF's plain-text extraction.
    """
    s = _IMG.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _FOOTNOTE_REF.sub("", s)
    s = _FENCE.sub("", s)
    s = _HTML_TAG.sub("", s)

    # Math / LaTeX. Inline math first, then commands, then sub/superscripts.
    s = _INLINE_MATH.sub(lambda m: m.group(1), s)
    # repeat in case nested
    s = _INLINE_MATH.sub(lambda m: m.group(1), s)
    # Order matters:
    #   1. typeface wrappers first (`\mathbb{R}` → `R`) — otherwise the
    #      backslash-letter pass below eats `\mathbb`.
    #   2. backslash-letter substitutions (`\theta` → θ, `\in` → ∈) BEFORE
    #      sub/superscript stripping — otherwise `\theta_{x}` reduces to
    #      `thetax` and the Greek lookup fails (no word boundary).
    s = _LATEX_FRAC.sub(lambda m: f"{m.group(1)}/{m.group(2)}", s)
    # Wrap the substituted content with thin-spaces so adjacent commands like
    # `\in\mathbb{R}` don't fuse to `\inR` (which then masks `\in`'s
    # unicode lookup). The trailing whitespace is collapsed at the end.
    s = _LATEX_TYPEFACE_WRAPPER.sub(lambda m: f" {m.group(1)} ", s)
    s = _LATEX_CMD.sub(lambda m: f" {m.group(1)} ", s)
    s = _BACKSLASH_LETTER.sub(
        lambda m: _LATEX_UNICODE.get(m.group(1), m.group(1)), s
    )
    s = _LATEX_SUBSCRIPT.sub(lambda m: m.group(1), s)
    s = _LATEX_SUPERSCRIPT.sub(lambda m: m.group(1), s)
    s = _LATEX_BRACES.sub(lambda m: m.group(1), s)

    # Headings / lists / tables
    s = _HEADING_PREFIX.sub("", s)
    s = _BULLET_PREFIX.sub("", s)
    s = _TABLE_SEP_ROW.sub("", s)
    s = _TABLE_PIPE.sub(" ", s)

    # Bold/italic last (keep contents)
    s = _BOLD_OR_ITALIC.sub(lambda m: m.group(1), s)

    # Whitespace collapse
    s = _MULTI_WS.sub(" ", s).strip()
    return s


# ----------------------------------------------------------------------
# pymupdf plain-text extraction (mirrors react-pdf-highlighter's view)
# ----------------------------------------------------------------------


def extract_pdf_plain_text(s3_key: str) -> str:
    import pymupdf  # type: ignore

    pdf_bytes = s3_service.get_object_bytes(s3_key)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        chunks: List[str] = []
        for page in doc:
            chunks.append(page.get_text("text"))
        return "\n".join(chunks)
    finally:
        doc.close()


# ----------------------------------------------------------------------
# Candidate snippet picker
# ----------------------------------------------------------------------


_CANDIDATE_PATTERNS = [
    ("plain", re.compile(r"^[A-Z][^\n]{60,200}\.$")),
    ("bold", re.compile(r"\*\*[^*\n]{20,200}\*\*")),
    ("inline_math", re.compile(r"\$[^$\n]{2,100}\$")),
    ("table_row", re.compile(r"^\|[^\n]+\|[^\n]+\|$", re.MULTILINE)),
    ("heading", re.compile(r"^#{2,4}\s+[^\n]{10,80}$", re.MULTILINE)),
]


def pick_candidates(markdown: str, per_kind: int = 5) -> List[Tuple[str, str]]:
    """Return [(kind, raw_snippet)] sampled across kinds."""
    out: List[Tuple[str, str]] = []
    for kind, pat in _CANDIDATE_PATTERNS:
        found = pat.findall(markdown)[:per_kind]
        # findall on a regex with no groups returns matches; with groups returns groups
        for snippet in found:
            if isinstance(snippet, tuple):
                snippet = snippet[0]
            out.append((kind, snippet))
    return out


def normalize_pdf_text(s: str) -> str:
    """Same whitespace collapse as the highlighter would do, more or less."""
    return _MULTI_WS.sub(" ", s).strip()


def fuzzy_table_match(normalized: str, pdf_norm: str) -> bool:
    """Table rows in Mistral are 'cell | cell | cell'; in pymupdf they're
    'cell  cell  cell' but with line breaks too. Try: split on runs of
    whitespace, require all >2-char cells to appear in pdf in the same order
    within ~200 chars of each other.
    """
    cells = [c for c in normalized.split() if len(c) > 2 and not c.isnumeric()]
    if len(cells) < 2:
        return False
    pos = 0
    last_pos = 0
    for cell in cells:
        idx = pdf_norm.find(cell, pos)
        if idx == -1:
            return False
        if pos > 0 and idx - last_pos > 400:
            return False
        last_pos = idx
        pos = idx + len(cell)
    return True


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------


def run(limit_papers: int = 5) -> None:
    db = SessionLocal()
    try:
        papers = (
            db.query(Paper)
            .filter(Paper.parser == "mistral")
            .filter(Paper.s3_object_key.isnot(None))
            .limit(limit_papers)
            .all()
        )

        kind_total: Counter = Counter()
        kind_hit: Counter = Counter()
        misses: List[Tuple[str, str, str]] = []

        for paper in papers:
            ocr = paper.ocr or {}
            pages = ocr.get("pages") or []
            if not pages:
                continue

            md_full = "\n\n".join(p.get("markdown") or "" for p in pages)
            try:
                pdf_plain = extract_pdf_plain_text(str(paper.s3_object_key))
            except Exception as e:
                logger.warning("pymupdf failed for %s: %s", paper.id, e)
                continue
            pdf_norm = normalize_pdf_text(pdf_plain)

            cands = pick_candidates(md_full, per_kind=4)
            for kind, raw in cands:
                kind_total[kind] += 1
                normalized = normalize_for_match(raw)
                # Drop anything too short to give a meaningful match.
                if len(normalized) < 8:
                    continue
                hit = normalized in pdf_norm
                if not hit and kind == "table_row":
                    hit = fuzzy_table_match(normalized, pdf_norm)
                if hit:
                    kind_hit[kind] += 1
                else:
                    if len(misses) < 30:
                        misses.append((kind, raw[:120], normalized[:120]))

        print()
        print("=== Match rate by snippet kind ===")
        for kind in [k for k, _ in _CANDIDATE_PATTERNS]:
            t = kind_total[kind]
            h = kind_hit[kind]
            pct = (h / t * 100) if t else 0.0
            print(f"  {kind:14s}  {h:3d}/{t:3d}  ({pct:5.1f}%)")
        total = sum(kind_total.values())
        hit = sum(kind_hit.values())
        print(f"  {'TOTAL':14s}  {hit:3d}/{total:3d}  ({(hit/total*100 if total else 0):5.1f}%)")

        print()
        print("=== Sample misses ===")
        for kind, raw, normalized in misses[:15]:
            print(f"\n[{kind}]")
            print(f"  raw:    {raw}")
            print(f"  norm:   {normalized}")
    finally:
        db.close()


if __name__ == "__main__":
    run(limit_papers=int(os.environ.get("LIMIT", "8")))
