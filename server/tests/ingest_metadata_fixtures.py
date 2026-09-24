"""Recorded Crossref / OpenAlex / arXiv responses and page texts shared by
the metadata tests, plus an `httpx.MockTransport` router."""

import json
from pathlib import Path
from typing import Callable

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "ingest_metadata"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def fixture_json(name: str):
    return json.loads(fixture(name))


# Page 1 of the PNAS paper (abridged) and of the arXiv tutorial.
PNAS_PAGE_1 = """\
RESEARCH ARTICLE | SYSTEMS BIOLOGY
PNAS 2026 Vol. 123 No. 15 e2516511123 https://doi.org/10.1073/pnas.2516511123
Reconstruction of human metabolic models with
large language models
Jiahao Luoa,b, Hao Wangc, Devlin Moyerd, and Jens Nielsenc,1
Edited by Somebody; received May 1, 2025
Genome-scale metabolic models (GEMs) are ... as shown before
(doi:10.1038/nbt.2488).
"""

ARXIV_PAGE_1 = """\
How to use and interpret activation patching
Stefan Heimersheim∗
Neel Nanda
Abstract
Activation patching is a popular mechanistic interpretability technique.
arXiv:2404.15255v1  [cs.LG]  23 Apr 2024
"""


Handler = Callable[[httpx.Request], httpx.Response]


def router(routes: dict[str, object], calls: list[str] | None = None) -> Handler:
    """Answer by the first route whose key is a substring of the URL.

    A value is a JSON-able object, a str (Atom XML), an int status, or an
    exception to raise. Unrouted URLs 404.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for key, value in routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, int):
                    return httpx.Response(value)
                if isinstance(value, str):
                    return httpx.Response(200, text=value)
                return httpx.Response(200, json=value)
        return httpx.Response(404)

    return handle


def client_for(routes: dict[str, object], calls: list[str] | None = None):
    return httpx.AsyncClient(transport=httpx.MockTransport(router(routes, calls)))


PNAS_ROUTES = {
    "api.crossref.org/works/10.1073/pnas.2516511123": fixture_json(
        "crossref_pnas.json"
    ),
    "api.openalex.org/works/doi:10.1073/pnas.2516511123": fixture_json(
        "openalex_pnas.json"
    ),
}
ARXIV_ROUTES = {
    "export.arxiv.org/api/query": fixture("arxiv_2404.15255.xml"),
    "api.openalex.org/works/doi:10.48550/arxiv.2404.15255": fixture_json(
        "openalex_arxiv.json"
    ),
}
