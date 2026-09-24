"""Metadata from a web page's <head>: `citation_*`, OpenGraph/Twitter, JSON-LD.

Scholarly landing pages (OpenReview, ACL Anthology, publishers, arXiv) carry
Highwire `citation_*` tags; blogs carry OpenGraph/Twitter cards and often a
schema.org JSON-LD block (the Transluce blog has only `<title>`, a
description and JSON-LD). Parsed with the stdlib `html.parser`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

from app.ingest.sources.work_record import clean_text


@dataclass
class PageMeta:
    """Everything useful in one page's head. Keys are lowercased."""

    url: str
    meta: dict[str, list[str]] = field(default_factory=dict)
    title: Optional[str] = None  # <title>
    canonical: Optional[str] = None
    json_ld: list[dict[str, Any]] = field(default_factory=list)

    def first(self, *names: str) -> Optional[str]:
        for name in names:
            for value in self.meta.get(name, []):
                text = clean_text(value)
                if text:
                    return text
        return None

    def all(self, name: str) -> list[str]:
        return [t for v in self.meta.get(name, []) if (t := clean_text(v))]

    def absolute(self, url: Optional[str]) -> Optional[str]:
        return urljoin(self.url, url) if url else None


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.title_parts: list[str] = []
        self.canonical: Optional[str] = None
        self.json_ld: list[str] = []
        self._in_title = False
        self._in_json_ld = False
        self._script: list[str] = []
        self._seen_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        attr = {k.lower(): v or "" for k, v in attrs}
        if tag == "meta":
            name = attr.get("name") or attr.get("property") or attr.get("itemprop")
            content = attr.get("content")
            if name and content:
                self.meta.setdefault(name.strip().lower(), []).append(content)
        elif tag == "title" and not self._seen_title:
            self._in_title = True
        elif tag == "link" and "canonical" in attr.get("rel", "").lower().split():
            self.canonical = attr.get("href") or self.canonical
        elif tag == "script" and "ld+json" in attr.get("type", "").lower():
            self._in_json_ld = True
            self._script = []

    def handle_endtag(self, tag: str):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._seen_title = True
        elif tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._script))

    def handle_data(self, data: str):
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_json_ld:
            self._script.append(data)


def parse_head(html: str, url: str) -> PageMeta:
    """Parse the metadata out of `html` (a page fetched from `url`)."""
    parser = _HeadParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - keep whatever was parsed before the junk
        pass
    blocks: list[dict[str, Any]] = []
    for raw in parser.json_ld:
        try:
            blocks += _flatten_ld(json.loads(raw))
        except ValueError:
            continue
    return PageMeta(
        url=url,
        meta=parser.meta,
        title=clean_text("".join(parser.title_parts)),
        canonical=parser.canonical,
        json_ld=blocks,
    )


def _flatten_ld(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [d for item in value for d in _flatten_ld(item)]
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("@graph"), list):
        return _flatten_ld(value["@graph"])
    return [value]


# -- what the page is about --------------------------------------------------------

_ARTICLE_TYPES = {
    "article", "blogposting", "newsarticle", "scholarlyarticle", "techarticle",
    "report", "webpage", "creativework", "dataset", "softwaresourcecode",
}  # fmt: skip
# Interstitials and error pages: fetching "worked" but this isn't the page.
_JUNK_TITLE = re.compile(
    r"^(?:just a moment|attention required|access denied|forbidden|"
    r"40[0-9]\b|50[0-9]\b|page not found|not found|error\b|are you a robot|"
    r"security check|verify(?:ing)? you are human|(?:checking|verifying) your browser|"
    r"log ?in|sign ?in)|captcha",
    re.IGNORECASE,
)
_TITLE_SEPARATORS = re.compile(r"\s+(?:\||–|—|-|·|::)\s+")


def article_ld(page: PageMeta) -> Optional[dict[str, Any]]:
    """The JSON-LD block describing the page's article, if any."""
    for block in page.json_ld:
        types = block.get("@type")
        types = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t.lower() in _ARTICLE_TYPES for t in types):
            return block
    return None


def ld_names(value: Any) -> list[str]:
    """schema.org author/publisher value(s) -> names."""
    items = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else item
        text = clean_text(name) if isinstance(name, str) else None
        if text and not text.startswith("http"):
            names.append(text)
    return names


def ld_text(block: Optional[dict[str, Any]], *keys: str) -> Optional[str]:
    for key in keys:
        value = (block or {}).get(key)
        if isinstance(value, dict):
            value = value.get("url") or value.get("name")
        if isinstance(value, list) and value:
            value = value[0]
            if isinstance(value, dict):
                value = value.get("url") or value.get("name")
        text = clean_text(value) if isinstance(value, str) else None
        if text:
            return text
    return None


def is_junk_title(title: Optional[str]) -> bool:
    return not title or bool(_JUNK_TITLE.search(title))


def host_name(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


def split_title(title: str) -> tuple[str, Optional[str]]:
    """'Post Title | Site Name' -> ('Post Title', 'Site Name')."""
    parts = _TITLE_SEPARATORS.split(title)
    if len(parts) >= 2 and len(parts[-1].split()) <= 4 and len(parts[0]) >= 8:
        return " - ".join(parts[:-1]), parts[-1]
    return title, None


def person_name(value: str) -> str:
    """'Vaswani, Ashish' (citation_author style) -> 'Ashish Vaswani'."""
    if value.count(",") == 1:
        family, given = (p.strip() for p in value.split(","))
        if family and given:
            return f"{given} {family}"
    return value
