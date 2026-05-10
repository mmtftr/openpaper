import html
import logging
import re
import unicodedata
from typing import Any, Iterable, Optional

import pymupdf  # type: ignore

from src.schemas import AIHighlight

logger = logging.getLogger(__name__)

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
    "­": "",
    "​": "", "‌": "", "‍": "",
    "﻿": "",
    "·": "•", "∙": "•", "⋅": "•",
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
})


def _canonicalize(text: str) -> str:
    return unicodedata.normalize("NFC", html.unescape(text)).translate(_PUNCT_FOLD)


def _normalize_for_match(text: str) -> str:
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


def _normalize_pdf_text(text: str) -> str:
    text = _canonicalize(text or "")
    text = _HTML_CLOSE_TAG.sub("", text)
    text = text.replace("*", "")
    return _MULTI_WS.sub(" ", text).strip()


def _compact_find(needle: str, haystack: str, drop_predicate) -> Optional[str]:
    needle_compact = "".join(c for c in needle if not drop_predicate(c))
    if len(needle_compact) < 8:
        return None

    pos_map: list[int] = []
    haystack_compact_chars: list[str] = []
    for i, char in enumerate(haystack):
        if not drop_predicate(char):
            pos_map.append(i)
            haystack_compact_chars.append(char)

    haystack_compact = "".join(haystack_compact_chars)
    index = haystack_compact.lower().find(needle_compact.lower())
    if index < 0:
        return None

    start = pos_map[index]
    end = pos_map[index + len(needle_compact) - 1] + 1
    return haystack[start:end]


def _find_in_pdf_text(quote: str, page_text: str) -> Optional[str]:
    needle = _normalize_for_match(quote)
    if len(needle) < 4:
        return None

    haystack = _normalize_pdf_text(page_text)
    haystack_lower = haystack.lower()
    needle_lower = needle.lower()
    if needle_lower in haystack_lower:
        start = haystack_lower.index(needle_lower)
        return haystack[start : start + len(needle)]

    rejoined_keep = _DEHYPHEN.sub(r"\1-\2", haystack)
    if rejoined_keep != haystack and needle_lower in rejoined_keep.lower():
        return needle

    rejoined_drop = _DEHYPHEN.sub(r"\1\2", haystack)
    if rejoined_drop != haystack and needle_lower in rejoined_drop.lower():
        return needle

    section_number = _SECTION_NUM_PREFIX.match(needle)
    if section_number and "." not in section_number.group(1):
        with_period = f"{section_number.group(1)}. {needle[section_number.end():]}"
        if with_period.lower() in haystack_lower:
            return with_period

    if len(needle) >= 12:
        match = _compact_find(needle, haystack, str.isspace)
        if match is not None:
            return match

    if len(needle) >= 30:
        match = _compact_find(
            needle,
            haystack,
            lambda char: char.isspace() or char in _PUNCT_NOISE,
        )
        if match is not None:
            return match

    return None


def _scaled_rect(rect: Any, page_number: int) -> dict[str, float | int]:
    return {
        "x1": float(rect.x0),
        "y1": float(rect.y0),
        "x2": float(rect.x1),
        "y2": float(rect.y1),
        "width": float(rect.width),
        "height": float(rect.height),
        "pageNumber": page_number,
    }


def _scaled_position(rects: Iterable[Any], page_number: int) -> Optional[dict[str, Any]]:
    scaled_rects = [_scaled_rect(rect, page_number) for rect in rects]
    if not scaled_rects:
        return None

    x1 = min(rect["x1"] for rect in scaled_rects)
    y1 = min(rect["y1"] for rect in scaled_rects)
    x2 = max(rect["x2"] for rect in scaled_rects)
    y2 = max(rect["y2"] for rect in scaled_rects)
    bounding_rect = {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": x2 - x1,
        "height": y2 - y1,
        "pageNumber": page_number,
    }
    return {
        "boundingRect": bounding_rect,
        "rects": scaled_rects,
        "usePdfCoordinates": True,
    }


def anchor_ai_highlights(
    pdf_file_path: str,
    highlights: list[AIHighlight],
) -> list[Optional[dict[str, Any]]]:
    """Best-effort PyMuPDF anchoring for assistant highlights."""
    anchors: list[Optional[dict[str, Any]]] = [None] * len(highlights)
    if not highlights:
        return anchors

    doc = pymupdf.open(pdf_file_path)
    try:
        for index, highlight in enumerate(highlights):
            if not highlight.text:
                continue

            for page_index, page in enumerate(doc):
                page_number = page_index + 1
                page_text = page.get_text("text")  # type: ignore
                matched_text = _find_in_pdf_text(highlight.text, page_text)
                if not matched_text:
                    continue

                search_terms = [highlight.text]
                if matched_text not in search_terms:
                    search_terms.append(matched_text)

                for term in search_terms:
                    rects = page.search_for(term)  # type: ignore
                    position = _scaled_position(rects, page_number)
                    if position:
                        anchors[index] = {
                            "page_number": page_number,
                            "position": position,
                        }
                        break

                if anchors[index]:
                    break
    except Exception:
        logger.warning("Failed to anchor AI highlights", exc_info=True)
    finally:
        doc.close()

    return anchors
