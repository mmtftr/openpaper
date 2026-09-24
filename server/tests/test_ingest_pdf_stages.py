"""PDF stages (source / text_layer / preview / figures) and `app.ingest.pdf`.

Fixture PDFs are generated with pymupdf in the test; S3 is an in-memory
fake. The DB-writing `save()`s are covered by `test_ingest_pdf_stages_db.py`.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image

from app.core.deadline import Deadline
from app.core.errors import ErrorKind, PermanentError, TemporaryError, classify
from app.database.models import Paper
from app.ingest import storage
from app.ingest.pdf.document import InvalidPdfError, inspect_pdf
from app.ingest.pdf.figures import FigureBox, render_figures
from app.ingest.pdf.render import render_preview
from app.ingest.pdf.text import (
    extract_text_layer,
    find_arxiv_id,
    find_doi,
    split_authors,
)
from app.ingest.stages import figures as figures_stage
from app.ingest.stages.base import StageContext
from app.ingest.stages.figures import Figures
from app.ingest.stages.preview import Preview
from app.ingest.stages.source import Source, store_source
from app.ingest.stages.text_layer import TextLayer, apply_embedded_metadata

RED = (1, 0, 0)

# -- fixtures -------------------------------------------------------------------


def make_pdf(
    pages: int = 2,
    *,
    metadata: dict[str, str] | None = None,
    xmp: str | None = None,
    rotate_first: int = 0,
) -> bytes:
    """Letter-size pages with text; page 1 has a red 100x50 pt box at (100, 200)."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Hello from page {n + 1}")
        if n == 0:
            page.draw_rect(pymupdf.Rect(100, 200, 200, 250), color=RED, fill=RED)
            if rotate_first:
                page.set_rotation(rotate_first)
    if metadata:
        doc.set_metadata(metadata)
    if xmp:
        doc.set_xml_metadata(xmp)
    data = doc.tobytes()
    doc.close()
    return data


def xmp_packet(body: str) -> str:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about=""'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
        f"{body}</rdf:Description></rdf:RDF></x:xmpmeta>"
    )


class FakeS3:
    """What `storage` uses of `app.helpers.s3.S3Service`."""

    bucket_name = "bucket"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.s3_client = SimpleNamespace(put_object=self._put)

    def _put(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[Key] = (Body, ContentType)

    def get_object_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def _public_url(self, key: str) -> str:
        return f"https://files.test/{key}"


def make_ctx(s3: FakeS3, paper_id: uuid.UUID | None = None) -> StageContext:
    return StageContext(
        paper_id=paper_id or uuid.uuid4(),
        stage="test",
        attempt=1,
        is_supplementary=False,
        deadline=Deadline(30),
        s3=s3,  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
def stored_pdf(monkeypatch):
    """A FakeS3 holding a PDF, and `load_pdf` pointed at it (no DB)."""
    s3 = FakeS3()
    pdf = make_pdf()
    s3.objects["papers/x/paper.pdf"] = (pdf, "application/pdf")
    monkeypatch.setattr(storage, "_paper_pdf_key", lambda ctx: "papers/x/paper.pdf")
    return s3


def near(size: tuple[Any, Any], expected: tuple[float, float]) -> bool:
    """pymupdf rounds pixel bounds outward: allow one pixel."""
    return all(abs(a - round(b)) <= 1 for a, b in zip(size, expected))


def png_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def png_center(data: bytes) -> Any:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    return image.getpixel((image.width // 2, image.height // 2))


# -- document -------------------------------------------------------------------


def test_inspect_pdf_counts_pages():
    assert inspect_pdf(make_pdf(pages=3)) == 3


def _encrypted(user_pw: str | None) -> bytes:
    doc = pymupdf.open(stream=make_pdf(), filetype="pdf")
    return doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,  # pyright: ignore[reportAttributeAccessIssue]
        owner_pw="owner",
        user_pw=user_pw or "",
        permissions=0,
    )


@pytest.mark.parametrize(
    "data,message",
    [
        (b"", "empty"),
        (b"just some text, not a pdf", "not a readable PDF"),
        (make_pdf()[:200], "not a readable PDF"),
        (_encrypted("secret"), "password-protected"),
    ],
)
def test_inspect_pdf_rejects_unreadable_files(data, message):
    with pytest.raises(InvalidPdfError, match=message) as info:
        inspect_pdf(data)
    assert classify(info.value).kind == ErrorKind.PERMANENT


def test_owner_password_only_pdf_is_accepted():
    assert inspect_pdf(_encrypted(None)) == 2


# -- text layer + embedded metadata ------------------------------------------------


def test_text_layer_per_page_with_sizes():
    result = extract_text_layer(make_pdf(pages=2, rotate_first=90))
    assert [p.page_no for p in result.pages] == [1, 2]
    assert "Hello from page 1" in result.pages[0].text
    assert "Hello from page 2" in result.pages[1].text
    # Rotation applied: the rotated page is landscape.
    assert (result.pages[0].width_pt, result.pages[0].height_pt) == (792, 612)
    assert (result.pages[1].width_pt, result.pages[1].height_pt) == (612, 792)
    assert result.embedded.is_empty()


def test_embedded_metadata_from_info_dict():
    pdf = make_pdf(
        metadata={
            "title": "Deep Learning for Things",
            "author": "Alice Smith; Bob Jones",
            "subject": "Nature 2020, doi:10.1038/s41586-020-2649-2.",
            "keywords": "arXiv:2512.11949v2, learning",
        }
    )
    embedded = extract_text_layer(pdf).embedded
    assert embedded.title == "Deep Learning for Things"
    assert embedded.authors == ["Alice Smith", "Bob Jones"]
    assert embedded.doi == "10.1038/s41586-020-2649-2"
    assert embedded.arxiv_id == "2512.11949"
    assert embedded.author_raw == "Alice Smith; Bob Jones"


def test_embedded_metadata_prefers_xmp_identifiers_and_creators():
    xmp = xmp_packet(
        "<dc:title><rdf:Alt><rdf:li xml:lang='x-default'>A Good Title Here"
        "</rdf:li></rdf:Alt></dc:title>"
        "<dc:creator><rdf:Seq><rdf:li>Carol Wu</rdf:li><rdf:li>Dan Li</rdf:li>"
        "</rdf:Seq></dc:creator>"
        "<prism:doi>10.48550/arXiv.2401.00001</prism:doi>"
    )
    pdf = make_pdf(
        metadata={
            "title": "Microsoft Word - draft3.docx",
            "subject": "see 10.9999/other",
        },
        xmp=xmp,
    )
    embedded = extract_text_layer(pdf).embedded
    assert embedded.title == "A Good Title Here"  # junk info title skipped
    assert embedded.authors == ["Carol Wu", "Dan Li"]
    assert embedded.doi == "10.48550/arXiv.2401.00001"
    assert embedded.arxiv_id == "2401.00001"


@pytest.mark.parametrize("title", ["untitled", "paper_final.pdf", "main.tex", "x"])
def test_placeholder_titles_are_dropped(title):
    embedded = extract_text_layer(make_pdf(metadata={"title": title})).embedded
    assert embedded.title is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Alice Smith, Bob Jones and Carol Wu",
            ["Alice Smith", "Bob Jones", "Carol Wu"],
        ),
        ("Alice Smith & Bob Jones", ["Alice Smith", "Bob Jones"]),
        ("Smith, J.", []),  # surname-comma-initials: ambiguous
        ("Smith, John; Doe, Jane", ["Smith, John", "Doe, Jane"]),
        ("Alice Smith", ["Alice Smith"]),
        ("", []),
    ],
)
def test_split_authors(raw, expected):
    assert split_authors(raw) == expected


@pytest.mark.parametrize(
    "text,doi",
    [
        ("https://doi.org/10.1145/3292500.3330701.", "10.1145/3292500.3330701"),
        ("(doi:10.1000/xyz123)", "10.1000/xyz123"),
        ("10.1016/0022-2836(81)90087-5", "10.1016/0022-2836(81)90087-5"),
        ("no identifier here", None),
    ],
)
def test_find_doi(text, doi):
    assert find_doi(text) == doi


@pytest.mark.parametrize(
    "text,arxiv_id",
    [
        ("arXiv:2512.11949v3", "2512.11949"),
        ("https://arxiv.org/abs/2401.12345", "2401.12345"),
        ("arXiv:hep-th/9901001v1", "hep-th/9901001"),
        ("version 2512.11949", None),  # needs the arXiv prefix
    ],
)
def test_find_arxiv_id(text, arxiv_id):
    assert find_arxiv_id(text) == arxiv_id


def test_apply_embedded_metadata_respects_sources():
    embedded = extract_text_layer(
        make_pdf(
            metadata={
                "title": "Embedded Title Of Paper",
                "author": "Alice Smith; Bob Jones",
                "subject": "doi:10.1234/abc",
            }
        )
    ).embedded
    paper = Paper(
        title="Title I Typed",
        doi="10.5555/crossref",
        metadata_source={"title": "user", "doi": "crossref"},
    )
    assert apply_embedded_metadata(paper, embedded) == ["authors"]
    assert paper.title == "Title I Typed"
    assert paper.doi == "10.5555/crossref"
    assert paper.authors == ["Alice Smith", "Bob Jones"]
    assert paper.metadata_source == {
        "title": "user",
        "doi": "crossref",
        "authors": "embedded",
    }

    # Empty fields and earlier embedded values are (re)written.
    fresh = Paper(metadata_source={"title": "embedded"}, title="Old Embedded")
    written = apply_embedded_metadata(fresh, embedded)
    assert set(written) == {"title", "authors", "doi"}
    assert fresh.title == "Embedded Title Of Paper"
    assert fresh.doi == "10.1234/abc"

    # A legacy value with no recorded source is left alone.
    legacy = Paper(title="From the old pipeline", metadata_source={})
    assert "title" not in apply_embedded_metadata(legacy, embedded)


# -- rendering --------------------------------------------------------------------


def test_preview_is_at_most_800_px_wide():
    image = render_preview(make_pdf())
    assert image.width == 800
    assert png_size(image.png) == (image.width, image.height)
    assert near((image.width, image.height), (800, 792 * 800 / 612))


def test_render_figures_crops_at_300_dpi():
    boxes = [
        FigureBox("a", 1, {"x0": 100, "y0": 200, "x1": 200, "y1": 250}),
        # Overshoots the page edge: clamped, not an error.
        FigureBox("b", 1, {"x0": 500, "y0": 700, "x1": 700, "y1": 900}),
        FigureBox("c", 1, {"x0": 700, "y0": 800, "x1": 900, "y1": 900}),  # off-page
        FigureBox("d", 5, {"x0": 0, "y0": 0, "x1": 10, "y1": 10}),  # no page 5
        FigureBox("e", 1, {"x0": 0}),  # malformed
    ]
    a, b, c, d, e = render_figures(make_pdf(), boxes)
    assert near((a.width, a.height), (100 * 300 / 72, 50 * 300 / 72))
    assert png_size(a.png) == (a.width, a.height)
    assert png_center(a.png) == (255, 0, 0)
    assert near((b.width, b.height), (112 * 300 / 72, 92 * 300 / 72))
    for failed in (c, d, e):
        assert failed.png is None and failed.error


def test_render_figures_uses_rotated_page_coordinates():
    # Page 1 rotated 90°: the red box (100,200)-(200,250) unrotated appears at
    # x = 792-250..792-200, y = 100..200 on the page as displayed.
    pdf = make_pdf(rotate_first=90)
    (image,) = render_figures(
        pdf, [FigureBox("r", 1, {"x0": 542, "y0": 100, "x1": 592, "y1": 200})], dpi=72
    )
    assert (image.width, image.height) == (50, 100)
    assert png_center(image.png) == (255, 0, 0)


# -- storage ----------------------------------------------------------------------


def test_keys_are_under_the_paper_prefix():
    pid = uuid.UUID(int=1)
    prefix = f"papers/{pid}/"
    assert storage.source_key(pid, "My Paper (v2).pdf") == prefix + "My_Paper_v2_.pdf"
    assert storage.source_key(pid, "2512.11949") == prefix + "2512.11949.pdf"
    assert storage.source_key(pid, None) == prefix + "paper.pdf"
    assert storage.source_key(pid, "../../etc/passwd") == prefix + "passwd.pdf"
    assert storage.preview_key(pid) == prefix + "preview.png"
    assert storage.figure_key(pid, "f1") == prefix + "figures/f1.png"
    assert storage.source_file_name(prefix + "2512.11949.pdf") == "2512.11949.pdf"


def _client_error(status: int, code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "PutObject",
    )


@pytest.mark.parametrize(
    "error,expected",
    [
        (_client_error(507, "XMinioStorageFull"), TemporaryError),
        (_client_error(503, "SlowDown"), TemporaryError),
        (_client_error(404, "NoSuchKey"), PermanentError),
        (_client_error(403, "AccessDenied"), PermanentError),
        (EndpointConnectionError(endpoint_url="http://minio:9000"), TemporaryError),
    ],
)
def test_s3_errors_are_classified(error, expected):
    def boom(**kwargs):
        raise error

    s3 = FakeS3()
    s3.s3_client = SimpleNamespace(put_object=boom)
    with pytest.raises(expected, match="S3 upload k failed"):
        storage.put_bytes(s3, "k", b"x", "image/png")  # pyright: ignore[reportArgumentType]


# -- stages -----------------------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def test_store_source_uploads_and_sets_paper_fields():
    s3, session = FakeS3(), FakeSession()
    pdf = make_pdf(pages=3)
    paper = Paper()
    stored = asyncio.run(store_source(session, paper, pdf, "Some Paper.pdf", s3=s3))  # pyright: ignore[reportArgumentType]

    assert paper.id is not None
    assert stored.s3_object_key == f"papers/{paper.id}/Some_Paper.pdf"
    assert s3.objects[stored.s3_object_key] == (pdf, "application/pdf")
    assert paper.s3_object_key == stored.s3_object_key
    assert paper.file_url == f"https://files.test/{stored.s3_object_key}"
    assert paper.page_count == 3
    assert paper.size_in_kb == len(pdf) // 1024
    assert session.added == [paper]


def test_store_source_rejects_before_uploading():
    s3, session = FakeS3(), FakeSession()
    with pytest.raises(InvalidPdfError, match="password-protected"):
        asyncio.run(store_source(session, Paper(), _encrypted("pw"), "x.pdf", s3=s3))  # pyright: ignore[reportArgumentType]
    assert s3.objects == {} and session.added == []


def test_source_run_rereads_page_count(stored_pdf):
    assert asyncio.run(Source().run(make_ctx(stored_pdf))) == 2


def test_text_layer_run(stored_pdf):
    progress: list[tuple[int, int]] = []
    ctx = make_ctx(stored_pdf)

    async def report(done: int, total: int) -> None:
        progress.append((done, total))

    ctx.report_progress = report
    result = asyncio.run(TextLayer().run(ctx))
    assert [p.page_no for p in result.pages] == [1, 2]
    assert progress == [(2, 2)]


def test_preview_run_uploads_png(stored_pdf):
    ctx = make_ctx(stored_pdf)
    url = asyncio.run(Preview().run(ctx))
    key = f"papers/{ctx.paper_id}/preview.png"
    assert url == f"https://files.test/{key}"
    body, content_type = stored_pdf.objects[key]
    assert content_type == "image/png" and png_size(body)[0] == 800


def test_figures_run_uploads_each_renderable_box(stored_pdf, monkeypatch):
    ok, bad = str(uuid.uuid4()), str(uuid.uuid4())
    boxes = [
        FigureBox(ok, 1, {"x0": 100, "y0": 200, "x1": 200, "y1": 250}),
        FigureBox(bad, 9, {"x0": 0, "y0": 0, "x1": 10, "y1": 10}),
    ]
    monkeypatch.setattr(figures_stage, "load_boxes", lambda session, pid: boxes)
    ctx = make_ctx(stored_pdf)
    ctx.session_factory = lambda: SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        execute=lambda *a: None, rollback=lambda: None, close=lambda: None
    )

    stored = asyncio.run(Figures().run(ctx))
    assert [f.figure_id for f in stored] == [ok]
    key = f"papers/{ctx.paper_id}/figures/{ok}.png"
    assert stored[0].s3_key == key
    assert near((stored[0].width, stored[0].height), (100 * 300 / 72, 50 * 300 / 72))
    assert png_center(stored_pdf.objects[key][0]) == (255, 0, 0)


def test_figures_run_with_no_boxes_does_nothing(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setattr(figures_stage, "load_boxes", lambda session, pid: [])
    ctx = make_ctx(s3)
    ctx.session_factory = lambda: SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue]
        execute=lambda *a: None, rollback=lambda: None, close=lambda: None
    )
    assert asyncio.run(Figures().run(ctx)) == []
    assert s3.objects == {}
