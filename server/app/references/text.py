"""Pure text helpers for bibliography entries: cache keys, URLs, titles, years.

Entries come from the reader's PDF text extraction, so they carry the PDF's
line breaks as spaces: a URL broken after a slash arrives as
`https://transluce.org/ user-modeling`. `url_candidates` repairs that.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional

# "[12] " / "12. " numbering: the same reference is numbered differently in
# every paper, so it is not part of the cache key.
_LEADING_NUMBER = re.compile(r"^\s*(?:\[\s*\d{1,4}\s*\]|\d{1,4}\.)\s*")


def normalize_entry(text: str) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed (the cache key's input)."""
    folded = unicodedata.normalize("NFKC", _LEADING_NUMBER.sub("", text)).lower()
    folded = re.sub(r"[^\w\s]", "", folded)
    return re.sub(r"\s+", " ", folded).strip()


def cache_key(text: str) -> str:
    return hashlib.sha256(normalize_entry(text).encode()).hexdigest()


# -- URLs -----------------------------------------------------------------------

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'{}|\\^`]+", re.IGNORECASE)
_TRAILING = ".,;:'\"”’»"
# A URL whose last character is one of these was probably cut by a line break.
_OPEN_ENDINGS = ("/", "-", "_", "=", "?", "&", "#", "~")
# What the text right after such a URL looks like when it continues the path.
_CONTINUATION = re.compile(r"^/?[A-Za-z0-9][\w\-.~%/?#=&+:@!$*]*$")
# ...and words that follow a URL in a reference without continuing it.
_NOT_PATH = frozenset(
    {"accessed", "retrieved", "visited", "last", "and", "in", "the", "url",
     "doi", "arxiv", "pp", "vol", "online", "available", "at"}
)  # fmt: skip
_MAX_JOINS = 3


def strip_url(raw: str) -> str:
    """Drop sentence punctuation and unbalanced closing brackets from a URL."""
    url = raw
    while url:
        if url[-1] in _TRAILING:
            url = url[:-1]
        elif url[-1] in ")]" and url.count(url[-1]) > url.count(
            "(" if url[-1] == ")" else "["
        ):
            url = url[:-1]
        else:
            break
    return url


def _continues(url: str, token: str) -> bool:
    if not _CONTINUATION.match(token) or token.lower() in _NOT_PATH:
        return False
    if re.fullmatch(r"(?:19|20)\d{2}[a-z]?", token):
        return False  # "..., 2025": the year, not a path segment
    return url.endswith(_OPEN_ENDINGS) or token.startswith("/")


def _url_spans(text: str) -> list[tuple[int, int, list[str]]]:
    """(start, end, [repaired, original]) for each URL; end covers the join."""
    spans: list[tuple[int, int, list[str]]] = []
    for match in _URL.finditer(text):
        raw = match.group(0)
        original = strip_url(raw)
        joined, end, clean_end = original, match.end(), raw == original
        for _ in range(_MAX_JOINS):
            if not clean_end:
                break  # "https://x.org/." — the sentence ended there
            following = re.match(r"\s+(\S+)", text[end:])
            if not following:
                break
            token = strip_url(following.group(1))
            if not _continues(joined, token):
                break
            joined += token
            end += following.end()
            clean_end = token == following.group(1)
        variants = [joined, original] if joined != original else [original]
        variants = [_with_scheme(v) for v in variants if _plausible(v)]
        if variants:
            spans.append((match.start(), end, variants))
    return spans


def url_candidates(text: str) -> list[list[str]]:
    """Every URL in `text`, each as [repaired, original] (or just [original]).

    The repaired form joins text that looks like a path continuation after a
    URL that ends mid-path (`.../` then `user-modeling`); both are returned
    because a join can be wrong, and the caller tries them in order.
    """
    found: list[list[str]] = []
    for _, _, variants in _url_spans(text):
        if variants not in found:
            found.append(variants)
    return found


def _with_scheme(url: str) -> str:
    return url if re.match(r"https?://", url, re.I) else f"https://{url}"


def _plausible(url: str) -> bool:
    host = re.sub(r"^(?:https?://)?", "", url, flags=re.I).split("/", 1)[0]
    return "." in host and len(host) > 3


def without_urls(text: str) -> str:
    """`text` with its URLs (including repaired continuations) blanked out."""
    out, last = [], 0
    for start, end, _ in _url_spans(text):
        out.append(text[last:start])
        last = end
    out.append(text[last:])
    return re.sub(r"\s+", " ", " ".join(out)).strip()


# -- title guesses --------------------------------------------------------------

# Leading "Bostrom, N." / "Vaswani, A., Shazeer, N., and ..." author block.
_AUTHOR_PREFIX = re.compile(
    r"^(?:[A-Z][\w'’-]*,?\s+(?:[A-Z]\.\s*)+(?:,\s*)?(?:and\s+|&\s*)?){1,12}"
)
# Venue / publication tails, which are never the title. `in` only counts
# when a venue word follows, so titles like "In Defense of ..." survive.
_STOP = re.compile(
    r"^(?:in\s+(?:proc\b|proceedings|advances|workshop|conf\b|conference|"
    r"international|the\s+proceedings)|proceedings|advances|journal|vol\b|volume|"
    r"pages|number|no\b|acm|ieee|arxiv|preprint|technical report|phd thesis|"
    r"springer|elsevier|oxford|cambridge|mit press|url\b|doi\b|accessed|https?:)",
    re.IGNORECASE,
)
_MONTHS = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)"
)


def title_candidates(text: str, limit: int = 2) -> list[str]:
    """Likely titles in a reference entry, best first.

    Sentence split only (never on commas, which titles contain), skipping the
    author block, venue tails and author-list-looking sentences. The caller
    verifies any search hit against the whole entry, so a bad guess costs a
    search, never a wrong answer.
    """
    cleaned = re.sub(r"\s+", " ", _LEADING_NUMBER.sub("", without_urls(text))).strip()
    queries: list[str] = []
    quoted = re.search(r"[“\"]([^”\"]{20,200})[”\"]", cleaned)
    if quoted:
        queries.append(quoted.group(1).strip(" ,."))

    source = _AUTHOR_PREFIX.sub("", cleaned).strip() or cleaned
    # Author-year style: "Smith, J. (2020). Title ..."
    source = re.sub(r"^.*?\((?:19|20)\d{2}[a-z]?\)\.?\s+(?=[A-Z])", "", source)
    segments = re.split(r"\.(?=\s|[A-Z])|(?<=\w)\?\s+(?=[A-Z])", source)
    for seg in (s.strip() for s in segments):
        words = seg.split()
        if len(words) < 3 or len(words) > 30 or _STOP.search(seg):
            continue
        if not re.search(r"[a-z]{3}", seg, re.I) or not seg[0].isalpha():
            continue
        if re.match(r"(?:and|et al|pp|see)\b", seg, re.I) or " et al" in seg:
            continue
        if len(re.findall(r"\b[A-Z]\.\s", seg + " ")) >= 2:
            continue  # initials: an author list
        capitalized = sum(1 for w in words if w[:1].isupper())
        if seg.count(",") >= 2 and capitalized / len(words) > 0.6:
            continue  # "Dami Choi, Vincent Huang, and Jacob Steinhardt"
        title = re.sub(r"[\"“”]", "", seg)
        title = re.sub(
            rf",?\s+(?:{_MONTHS}\s+)?(?:19|20)\d{{2}}[a-z]?\.?$", "", title
        ).strip(" ,.")
        if len(title) >= 12:
            queries.append(title)
    return list(dict.fromkeys(queries))[:limit]


def year_in(text: str) -> Optional[int]:
    """The publication year of an entry: the first plausible 4-digit year."""
    for match in re.finditer(
        r"(?<![\d./-])((?:19|20)\d{2})(?![\d/]|\.\d)", without_urls(text)
    ):
        return int(match.group(1))
    return None
