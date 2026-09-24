"""The PDF's own text layer (per page) and its embedded metadata.

`extract_text_layer(pdf_bytes)` is the `text_layer` stage's CPU work.

Embedded metadata comes from the info dictionary (`doc.metadata`) and the
XMP packet (`doc.get_xml_metadata()`). Only values that look real are kept:
a title like "Microsoft Word - draft3.docx" is dropped, authors are only
split when the separator is unambiguous, and DOI / arXiv ids must match
their formats. The `metadata` stage treats all of it as hints to verify.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import pymupdf

from app.ingest.pdf.document import open_pdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageText:
    page_no: int  # 1-based
    width_pt: float  # `page.rect` (rotation applied), PDF points
    height_pt: float
    text: str  # `page.get_text("text")`, NUL bytes removed


@dataclass(frozen=True)
class EmbeddedMetadata:
    """What the PDF says about itself. Every field may be empty."""

    title: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    doi: Optional[str] = None  # "10.1234/abc", no "doi:" / URL prefix
    arxiv_id: Optional[str] = None  # "2512.11949" or "hep-th/9901001", no version
    # Raw strings, for the metadata stage's own searching if it wants them.
    author_raw: Optional[str] = None
    subject: Optional[str] = None
    keywords: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.title or self.authors or self.doi or self.arxiv_id)


@dataclass(frozen=True)
class TextLayerResult:
    pages: list[PageText]
    embedded: EmbeddedMetadata


def extract_text_layer(pdf_bytes: bytes) -> TextLayerResult:
    """Text per page + embedded metadata. Picklable in and out."""
    doc = open_pdf(pdf_bytes)
    try:
        pages = [_page_text(page) for page in doc]
        embedded = read_embedded_metadata(doc)
    finally:
        doc.close()
    return TextLayerResult(pages=pages, embedded=embedded)


def _page_text(page: pymupdf.Page) -> PageText:
    try:
        text = str(page.get_text("text") or "")  # type: ignore[attr-defined]
    except Exception as exc:  # one bad page shouldn't lose the others
        logger.warning("pymupdf failed on page %d: %s", page.number + 1, exc)  # type: ignore[operator]
        text = ""
    rect = page.rect
    return PageText(
        page_no=page.number + 1,  # type: ignore[operator]
        width_pt=float(rect.width),
        height_pt=float(rect.height),
        # Postgres text columns reject NUL bytes.
        text=text.replace("\x00", ""),
    )


# -- embedded metadata ----------------------------------------------------------


def read_embedded_metadata(doc: pymupdf.Document) -> EmbeddedMetadata:
    info = doc.metadata or {}
    try:
        xmp = doc.get_xml_metadata() or ""
    except Exception:
        xmp = ""

    info_title = _clean(info.get("title"))
    info_author = _clean(info.get("author"))
    subject = _clean(info.get("subject"))
    keywords = _clean(info.get("keywords"))

    title = _plausible_title(info_title) or next(
        filter(None, (_plausible_title(t) for t in _xmp_values(xmp, "dc:title"))),
        None,
    )
    authors = [a for a in map(_clean, _xmp_values(xmp, "dc:creator")) if a]
    if not authors and info_author:
        authors = split_authors(info_author)

    # Explicit identifier fields first, then free text.
    doi_candidates = [
        *(
            v
            for tag in ("prism:doi", "pdfx:doi", "crossmark:DOI", "dc:identifier")
            for v in _xmp_values(xmp, tag)
        ),
        subject or "",
        keywords or "",
        info_title or "",
        xmp,
    ]
    doi = next(filter(None, map(find_doi, doi_candidates)), None)

    arxiv_id = arxiv_id_from_doi(doi) if doi else None
    if arxiv_id is None:
        arxiv_candidates = [subject or "", keywords or "", info_title or "", xmp]
        arxiv_id = next(filter(None, map(find_arxiv_id, arxiv_candidates)), None)

    return EmbeddedMetadata(
        title=title,
        authors=authors,
        doi=doi,
        arxiv_id=arxiv_id,
        author_raw=info_author,
        subject=subject,
        keywords=keywords,
    )


def _clean(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = " ".join(html.unescape(value).replace("\x00", "").split())
    return value or None


_JUNK_TITLE = re.compile(
    r"^(microsoft\s+\w+\s+-|untitled|no\s+title|title$|document\d*$|slide\s*\d*$)",
    re.IGNORECASE,
)
_FILE_NAME = re.compile(r"\.(pdf|docx?|tex|dvi|ps|rtf|odt|indd|pptx?)$", re.IGNORECASE)


def _plausible_title(title: Optional[str]) -> Optional[str]:
    """`title` unless it's a placeholder or a file name."""
    title = _clean(title)
    if not title or len(title) < 4 or len(title) > 500:
        return None
    if _JUNK_TITLE.search(title) or _FILE_NAME.search(title):
        return None
    if " " not in title:  # one token: "paper_final", "arXiv:2501.01234", ...
        return None
    return title


def split_authors(raw: str) -> list[str]:
    """Split an info-dict author string, or return [] if it's ambiguous.

    "A Smith; B Jones" and "Alice Smith, Bob Jones and Carol Wu" split;
    "Smith, J." (surname-comma-initials) can't be told apart from two
    authors, so it isn't split at all.
    """
    raw = " ".join(raw.split())
    if not raw:
        return []
    if ";" in raw:
        return [p.strip() for p in raw.split(";") if p.strip()]
    parts = [
        p.strip()
        for p in re.split(r",\s*(?:and\s+)?|\s+and\s+|\s*&\s*", raw)
        if p.strip()
    ]
    if any(len(p.split()) < 2 for p in parts):
        return []
    return parts


def _xmp_values(xmp: str, tag: str) -> list[str]:
    """Text of `<tag>` elements (their `rdf:li` items if any) and `tag="..."`
    attributes in an XMP packet. Regex, not an XML parser: XMP in the wild
    is often malformed, and we only want a few simple values."""
    if not xmp or tag not in xmp:
        return []
    values: list[str] = []
    name = re.escape(tag)
    for body in re.findall(rf"<{name}\b[^>]*>(.*?)</{name}>", xmp, re.DOTALL):
        items = re.findall(r"<rdf:li\b[^>]*>(.*?)</rdf:li>", body, re.DOTALL)
        values.extend(items or [body])
    values.extend(re.findall(rf'{name}="([^"]*)"', xmp))
    out: list[str] = []
    for value in values:
        text = _clean(re.sub(r"<[^>]+>", " ", value))
        if text:
            out.append(text)
    return out


# -- identifiers ----------------------------------------------------------------

# Crossref's recommended pattern, loosened to find DOIs inside text.
_DOI = re.compile(r"\b(10\.\d{4,9}/[-._;()/:\w]+)", re.IGNORECASE)
_ARXIV_NEW = re.compile(
    r"arxiv(?:\.org)?[\s:/]*(?:abs/|pdf/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)
_ARXIV_OLD = re.compile(
    r"arxiv(?:\.org)?[\s:/]*(?:abs/|pdf/)?([a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def find_doi(text: str) -> Optional[str]:
    """The first DOI in `text`, without trailing punctuation."""
    match = _DOI.search(text or "")
    if not match:
        return None
    doi = match.group(1).rstrip(".,;:")
    # A trailing ")" belongs to the DOI only if it closes a "(" inside it,
    # e.g. 10.1016/0022-2836(81)90087-5 vs "(doi:10.1/x)".
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1].rstrip(".,;:")
    return doi or None


def find_arxiv_id(text: str) -> Optional[str]:
    """An arXiv id written with an "arXiv" prefix ("arXiv:2512.11949v2")."""
    text = text or ""
    match = _ARXIV_NEW.search(text) or _ARXIV_OLD.search(text)
    return match.group(1) if match else None


def arxiv_id_from_doi(doi: str) -> Optional[str]:
    """10.48550/arXiv.2512.11949 -> 2512.11949 (arXiv's own DOIs)."""
    match = _ARXIV_DOI.match(doi)
    return re.sub(r"v\d+$", "", match.group(1)) if match else None
