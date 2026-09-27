"""Where a paper's identity comes from: DOIs, arXiv ids and title guesses.

Pure functions (plus `read_pdf_hints`, which opens the PDF with pymupdf).
The metadata stages rank identifier candidates from the most to the least
trustworthy place (design §5):

1. the PDF's embedded metadata (info dictionary + XMP),
2. the filename,
3. the URL the PDF was downloaded from,
4. the text of pages 1-2 — page 1's header area first. DOIs further down
   (and anything after a "References" heading) are usually citations, so
   they come last; every candidate is still verified against page 1 before
   it is accepted, which is what makes trying a wrong one harmless.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional
from urllib.parse import unquote

# -- identifiers ----------------------------------------------------------------


@dataclass(frozen=True)
class Identifier:
    kind: Literal["doi", "arxiv"]
    value: str  # DOI lowercase without prefix; arXiv id without version
    where: str  # "embedded", "filename", "url", "page 1 header", ... (logging)

    def __str__(self) -> str:
        label = "DOI" if self.kind == "doi" else "arXiv"
        return f"{label} {self.value} ({self.where})"


# Crossref's recommended pattern, loosened for what PDF text does to DOIs.
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\x00-\x1f\x7f\"'<>{}|\\^`]+)", re.I)
# Zero-width spaces / soft hyphens publishers put inside long DOIs and URLs
# as line-break hints; removed before matching so the DOI stays whole.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]\n?")
_TRAILING = ".,;:'\"*"

# New-style arXiv id: YYMM.NNNNN (5 digits since 2015, 4 before).
_ARXIV_NEW = r"(\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})(?:v\d+)?"
# Old style: archive(.SUBJ)/YYMMNNN, e.g. hep-th/9901001, math.GT/0309136.
_ARXIV_OLD = r"([a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?/\d{2}(?:0[1-9]|1[0-2])\d{3})(?:v\d+)?"
# Only with an explicit arXiv marker: bare "1234.5678" in text is anything.
_ARXIV_MARKED = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.)"
    rf"(?:{_ARXIV_NEW}|{_ARXIV_OLD})",
    re.IGNORECASE,
)
# A filename that is just an arXiv id: "2404.15255v2.pdf".
_ARXIV_FILENAME = re.compile(rf"^(?:arxiv[_\-. ]?)?{_ARXIV_NEW}(?:\.pdf)?$", re.I)
_ARXIV_DOI_PREFIX = "10.48550/arxiv."

# Where page 1's header area ends / the bibliography starts.
_BODY_START = re.compile(
    r"^\s*(?:(?:1|I)\.?\s+)?introduction\b|^\s*a\s*b\s*s\s*t\s*r\s*a\s*c\s*t\b",
    re.IGNORECASE | re.MULTILINE,
)
_REFERENCES = re.compile(
    r"^\s*(?:#+\s*)?(?:references|bibliography|literature cited)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def clean_doi(raw: str) -> Optional[str]:
    """Normalize one DOI match: drop trailing punctuation and unbalanced
    closing brackets, lowercase. None if nothing DOI-shaped is left."""
    doi = unquote(raw).strip()
    while doi:
        if doi[-1] in _TRAILING:
            doi = doi[:-1]
        elif doi[-1] in ")]" and doi.count(doi[-1]) > doi.count(
            "(" if doi[-1] == ")" else "["
        ):
            doi = doi[:-1]
        else:
            break
    doi = doi.lower()
    return doi if re.fullmatch(r"10\.\d{4,9}/\S+", doi) else None


def identifiers_in(text: str, where: str) -> list[Identifier]:
    """Every DOI / arXiv id in `text`, in order of appearance.

    An arXiv DOI (10.48550/arXiv.X) is reported as the arXiv id X.
    """
    text = _INVISIBLE.sub("", text)
    found: list[tuple[int, Identifier]] = []
    for match in _ARXIV_MARKED.finditer(text):
        value = match.group(1) or match.group(2)
        found.append((match.start(), Identifier("arxiv", value, where)))
    for match in _DOI.finditer(text):
        doi = clean_doi(match.group(1))
        if doi and not doi.startswith(_ARXIV_DOI_PREFIX):
            found.append((match.start(), Identifier("doi", doi, where)))
    return [ident for _, ident in sorted(found, key=lambda item: item[0])]


def filename_identifiers(filename: Optional[str]) -> list[Identifier]:
    if not filename:
        return []
    name = unquote(filename).rsplit("/", 1)[-1]
    bare = _ARXIV_FILENAME.match(name)
    if bare:
        return [Identifier("arxiv", bare.group(1), "filename")]
    # "10.1038_s41586-021-03819-2.pdf": publishers' downloads swap / for _.
    doi_like = re.match(r"^(10\.\d{4,9})[_/](.+?)(?:\.pdf)?$", name, re.I)
    if doi_like:
        doi = clean_doi(f"{doi_like.group(1)}/{doi_like.group(2)}")
        if doi:
            return [Identifier("doi", doi, "filename")]
    return identifiers_in(name, "filename")


def url_identifiers(url: Optional[str]) -> list[Identifier]:
    """Identifiers in the URL the PDF came from (percent-decoded first)."""
    return identifiers_in(unquote(url), "url") if url else []


@dataclass(frozen=True)
class ArxivLink:
    """An arXiv paper someone pasted: `id` without version, `version` ("v2")
    only if the link named one."""

    id: str
    version: str = ""

    @property
    def pdf_url(self) -> str:
        return f"https://arxiv.org/pdf/{self.id}{self.version}"


_ARXIV_LINK_ID = (
    r"(?P<id>\d{2}(?:0[1-9]|1[0-2])\.\d{4,5}"
    r"|[a-z]+(?:-[a-z]+)?(?:\.[a-z]{2})?/\d{2}(?:0[1-9]|1[0-2])\d{3})"
    r"(?P<version>v\d+)?"
)
# arxiv.org / export.arxiv.org / alphaxiv.org pages: /abs/ID, /pdf/ID(.pdf),
# /html/ID, alphaXiv's /overview/ID; also doi.org/10.48550/arXiv.ID.
_ARXIV_LINK = re.compile(
    r"^(?:https?://)?(?:[\w-]+\.)*"
    r"(?:(?:arxiv|alphaxiv)\.org/(?:abs|pdf|html|overview|format)/"
    r"|(?:dx\.)?doi\.org/10\.48550/arxiv\.)"
    rf"{_ARXIV_LINK_ID}(?:\.pdf)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
# "arXiv:2504.11844v2", or a bare new-style id.
_ARXIV_TEXT = re.compile(rf"^(?:arxiv\s*:\s*)?{_ARXIV_LINK_ID}$", re.IGNORECASE)


def arxiv_link(text: str) -> Optional[ArxivLink]:
    """The arXiv paper `text` (a pasted URL or `arXiv:ID`) points at, if any."""
    text = text.strip()
    match = _ARXIV_LINK.match(text) or _ARXIV_TEXT.match(text)
    if not match:
        return None
    arxiv_id = match.group("id")
    if "/" in arxiv_id:  # old style: archive lowercase, subject class upper
        archive, number = arxiv_id.split("/")
        name, _, subject = archive.partition(".")
        arxiv_id = f"{name.lower()}{'.' + subject.upper() if subject else ''}/{number}"
    return ArxivLink(arxiv_id, (match.group("version") or "").lower())


def page_identifiers(pages: list[str]) -> list[Identifier]:
    """Identifiers from the first pages' text, best-first.

    Order: page 1 header area (before the abstract/introduction), the rest
    of page 1, then page 2. Anything after a references heading is dropped.
    """
    ranked: list[Identifier] = []
    for page_no, text in enumerate(pages[:2], start=1):
        refs = _REFERENCES.search(text)
        if refs:
            text = text[: refs.start()]
        if page_no == 1:
            body = _BODY_START.search(text)
            cut = body.start() if body else min(len(text), 1500)
            # The arXiv margin stamp ("arXiv:2404.15255v2 [cs.LG] 23 Apr 2024")
            # may be extracted anywhere on the page: it is header-grade.
            ranked += identifiers_in(text[:cut], "page 1 header")
            ranked += [
                ident
                for ident in identifiers_in(text[cut:], "page 1")
                if ident.kind == "arxiv"
            ]
            ranked += [
                ident
                for ident in identifiers_in(text[cut:], "page 1")
                if ident.kind == "doi"
            ]
        else:
            ranked += identifiers_in(text, f"page {page_no}")
    return ranked


def unique(identifiers: Iterable[Identifier]) -> list[Identifier]:
    """First occurrence of each (kind, value)."""
    seen: set[tuple[str, str]] = set()
    out: list[Identifier] = []
    for ident in identifiers:
        key = (ident.kind, ident.value.lower())
        if key not in seen:
            seen.add(key)
            out.append(ident)
    return out


# -- title guesses ------------------------------------------------------------

_JUNK_TITLE = re.compile(
    r"^(?:untitled|title|paper|article|main|manuscript|arxiv|preprint|document)"
    r"[\W\d_]*$|^microsoft word\b|\.(?:pdf|docx?|tex|dvi|ps)$|^[\W\d_]+$",
    re.IGNORECASE,
)
# Lines at the top of page 1 that aren't the title (journal headers, stamps).
_NOT_TITLE_LINE = re.compile(
    r"\barxiv:|\bdoi\b|https?://|www\.|@|©|\bcopyright\b|\bjournal of\b|"
    r"\bproceedings of\b|\bpreprint\b|\bunder review\b|\bconference paper\b|"
    r"\bpublished (?:as|in|by|online)\b|\breceived\b|\baccepted\b|\bvol\.|"
    r"\bvolume \d|\bissn\b|\bresearch article\b|"
    r"^\s*(?:abstract|introduction|contents|letter|article|research)\s*$",
    re.IGNORECASE,
)


def plausible_title(text: Optional[str]) -> Optional[str]:
    """`text` cleaned, if it could be a paper title."""
    if not text:
        return None
    title = re.sub(r"\s+", " ", text).strip(" .:-*#")
    letters = sum(ch.isalpha() for ch in title)
    if letters < 8 or len(title) > 300 or _JUNK_TITLE.search(title):
        return None
    return title


def title_from_text(page1: str) -> Optional[str]:
    """Rough title guess from plain page-1 text: the first substantial line
    near the top that doesn't look like a journal header or a stamp."""
    for line in page1.splitlines()[:15]:
        line = line.strip()
        if len(line.split()) < 3 or _NOT_TITLE_LINE.search(line):
            continue
        title = plausible_title(line)
        if title:
            return title
    return None


def title_from_markdown(page1: str) -> Optional[str]:
    """OCR markdown marks the title as its first heading."""
    for match in re.finditer(r"^#{1,3}\s+(.+)$", page1, re.MULTILINE):
        heading = match.group(1)
        if _NOT_TITLE_LINE.search(heading):
            continue
        title = plausible_title(heading)
        if title:
            return title
    return None


# -- text matching ------------------------------------------------------------


def tokens(text: Optional[str]) -> list[str]:
    """Lowercase ASCII word tokens: accents folded, ligatures expanded,
    words hyphenated across line breaks rejoined."""
    if not text:
        return []
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", folded.lower())


# -- the PDF itself -------------------------------------------------------------


@dataclass
class PdfHints:
    """What the PDF file says about itself (read by `read_pdf_hints`)."""

    identifiers: list[Identifier] = field(default_factory=list)
    embedded_title: Optional[str] = None
    embedded_keywords: list[str] = field(default_factory=list)
    # Page 1's largest-font text near the top: usually the title.
    layout_title: Optional[str] = None


def read_pdf_hints(pdf_bytes: bytes) -> PdfHints:
    """Embedded metadata + a layout-based title guess (runs in the CPU pool)."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        info = {k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str)}
        xmp = doc.get_xml_metadata() or ""
        layout_title = _layout_title(doc[0]) if doc.page_count else None

    # Identifiers anywhere in the info dictionary or XMP packet (prism:doi,
    # dc:identifier, crossmark, a DOI in /Subject, ...).
    blob = "\n".join([*info.values(), xmp])
    keywords = [
        k.strip()
        for k in re.split(r"[;,]", info.get("keywords", ""))
        if 1 < len(k.strip()) <= 80
    ]
    return PdfHints(
        identifiers=unique(identifiers_in(blob, "embedded")),
        embedded_title=plausible_title(info.get("title")),
        embedded_keywords=keywords[:20],
        layout_title=layout_title,
    )


def _layout_title(page) -> Optional[str]:
    """Join the lines set in page 1's biggest font (upper 60% of the page)."""
    lines: list[tuple[float, float, str]] = []  # (size, y, text)
    height = page.rect.height or 1.0
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            if tuple(round(d) for d in line.get("dir", (1, 0))) != (1, 0):
                continue  # rotated: the arXiv margin stamp
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if sum(ch.isalpha() for ch in text) < 3:
                continue
            y = line["bbox"][1]
            if y > 0.6 * height or _NOT_TITLE_LINE.search(text):
                continue
            lines.append((max(s.get("size", 0.0) for s in spans), y, text))
    if not lines:
        return None
    biggest = max(size for size, _, _ in lines)
    title_lines = sorted((y, t) for size, y, t in lines if size >= biggest - 0.5)
    return plausible_title(" ".join(t for _, t in title_lines))
