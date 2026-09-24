"""`app.ingest.content` helpers, the readers built on them, and the
`ingest_v2_data` migration's row mapping — no database needed."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.paper import detail as paper_detail
from app.ingest import content
from app.ingest.content import Figure, Page
from app.llm.chat import paper as chat_paper
from app.llm.tools import section_tools

PAPER_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _figure(page_no: int, image_id: str, label=None, s3_key="k") -> Figure:
    return Figure(
        id=uuid.uuid4(),
        ocr_image_id=image_id,
        page_no=page_no,
        label=label,
        caption=None,
        s3_key=s3_key,
    )


# -- full_text ------------------------------------------------------------------


def test_full_text_matches_the_legacy_raw_content_and_offsets():
    pages = [Page(1, "# Title\nbody"), Page(2, ""), Page(3, "end")]

    text, offsets = content.full_text(pages)

    # raw_content was the pages joined by a blank line; page_offset_map the
    # [start, end) of each page's own text.
    assert text == "# Title\nbody\n\n\n\nend"
    assert offsets == {1: (0, 12), 2: (14, 14), 3: (16, 19)}
    for page in pages:
        start, end = offsets[page.page_no]
        assert text[start:end] == page.markdown


def test_full_text_of_no_pages_is_empty():
    assert content.full_text([]) == ("", {})


# -- figures --------------------------------------------------------------------


def test_figures_sort_in_document_order_with_numeric_image_ids():
    figs = [
        _figure(3, "img-10.jpeg"),
        _figure(3, "img-9.jpeg"),
        _figure(1, "img-2.jpeg"),
    ]
    ordered = sorted(figs, key=content._document_order)
    assert [(f.page_no, f.ocr_image_id) for f in ordered] == [
        (1, "img-2.jpeg"),
        (3, "img-9.jpeg"),
        (3, "img-10.jpeg"),
    ]


def test_resolve_figure_prefers_ids_then_exact_labels_then_partial_labels():
    fig_1 = _figure(1, "img-0.jpeg", label="Figure 1")
    fig_3a = _figure(2, "img-1.jpeg", label="Figure 3a")
    table = _figure(2, "img-2.jpeg", label="Table 3")
    figs = [fig_1, fig_3a, table]

    assert content.resolve_figure(figs, "img-1.jpeg") is fig_3a
    assert content.resolve_figure(figs, str(table.id)) is table
    assert content.resolve_figure(figs, "Fig. 3a") is fig_3a
    assert content.resolve_figure(figs, "figure  1") is fig_1
    assert content.resolve_figure(figs, "3a") is fig_3a
    assert content.resolve_figure(figs, "Figure 9") is None


def test_repeated_mistral_ids_resolve_distinctly_by_row_id(monkeypatch):
    """Mistral restarts image ids per OCR batch, so two unlabeled figures on
    pages 1 and 17 are both "img-0.jpeg"; everything handed out uses the
    row id, which tells them apart. The bare Mistral id (saved chat
    history) still resolves, to the first."""
    first = _figure(1, "img-0.jpeg")
    later = _figure(17, "img-0.jpeg")
    figs = [first, later]

    assert content.resolve_figure(figs, str(first.id)) is first
    assert content.resolve_figure(figs, str(later.id)) is later
    assert content.resolve_figure(figs, "img-0.jpeg") is first

    outline = section_tools.build_outline(SimpleNamespace(), [], figs)  # type: ignore[arg-type]
    assert [f["id"] for f in outline["figures"]] == [str(first.id), str(later.id)]

    monkeypatch.setattr(section_tools, "_get_paper_or_raise", lambda *a, **k: None)
    monkeypatch.setattr(section_tools, "_load_figures", lambda db, paper: figs)
    got = section_tools.get_figure("p", str(later.id), None, None)  # type: ignore[arg-type]
    assert (got["id"], got["page"]) == (str(later.id), 17)
    assert got["url"] == f"/api/paper/p/figure/{later.id}"


# -- readers --------------------------------------------------------------------


def test_outline_lists_page_headings_and_figures():
    paper = SimpleNamespace(title="T", authors=["A"], page_count=2)
    pages = [Page(1, "# Intro\ntext\n## Setup"), Page(2, "# Results")]
    figs = [_figure(2, "img-0.jpeg", label="Figure 1", s3_key=None)]

    outline = section_tools.build_outline(paper, pages, figs)  # type: ignore[arg-type]

    assert outline["headings"] == [
        {"level": 1, "text": "Intro", "page": 1},
        {"level": 2, "text": "Setup", "page": 1},
        {"level": 1, "text": "Results", "page": 2},
    ]
    assert outline["figures"] == [
        {
            "label": "Figure 1",
            "page": 2,
            "caption": None,
            "id": str(figs[0].id),
            "available": False,
        }
    ]
    text = section_tools.render_outline_text(outline)
    assert "- Figure 1 (p. 2) [not yet rendered]" in text


def test_read_section_spans_pages_and_reports_the_heading_page(monkeypatch):
    pages = [Page(1, "# Intro\none"), Page(2, "two\n# Method\nthree"), Page(3, "four")]
    monkeypatch.setattr(section_tools, "_get_paper_or_raise", lambda *a, **k: None)
    monkeypatch.setattr(section_tools, "_load_pages", lambda db, p: pages)

    got = section_tools.read_section("p", "intro", None, None)  # type: ignore[arg-type]

    assert got == {
        "section": "Intro",
        "level": 1,
        "page": 1,
        "content": "# Intro\none\n\ntwo\n",
    }


def test_comprehensive_preload_stops_at_the_back_matter_page():
    pages = [Page(1, "# Intro"), Page(2, "body"), Page(3, "# References\n[1]")]
    paper = SimpleNamespace(abstract="")

    assert (
        chat_paper._select_preload("comprehensive", paper, pages)  # type: ignore[arg-type]
        == "# Intro\n\nbody"
    )
    assert chat_paper._select_preload("full", paper, pages).endswith("[1]")  # type: ignore[arg-type]


def test_markdown_payload_points_images_at_figure_rows(monkeypatch):
    # Mistral restarts image ids per OCR batch: page 1 and page 17 both
    # have an "img-0.jpeg".
    first = _figure(1, "img-0.jpeg")
    later = _figure(17, "img-0.jpeg")
    pages = [
        Page(1, "![img-0.jpeg](img-0.jpeg)\n"),
        Page(17, '![img-0.jpeg](img-0.jpeg "t") ![x](https://e.x/y.png)'),
    ]
    monkeypatch.setattr(content, "pages", lambda db, pid: pages)
    monkeypatch.setattr(content, "figures", lambda db, pid: [first, later])

    got = paper_detail._paper_markdown_payload(None, SimpleNamespace(id=PAPER_ID))  # type: ignore[arg-type]

    assert got.markdown == (
        f"![img-0.jpeg]({first.id})\n\n"
        f'![img-0.jpeg]({later.id} "t") ![x](https://e.x/y.png)'
    )
    assert got.source == "mistral"


# -- the data migration's row mapping --------------------------------------------


def _migration():
    path = next(
        Path(__file__).parent.parent.glob("migrations/versions/*_ingest_v2_data.py")
    )
    spec = importlib.util.spec_from_file_location("ingest_v2_data", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEGACY_PAGE = {
    "index": 0,
    "markdown": "repaired text",
    "mistral_markdown": "mistral text",
    "pymupdf_text": "layer text",
    "markdown_source": "openai_ocr_repair",
    "ocr_quality": {"status": "suspect"},
    "openai_ocr": {"model": "gpt-5.4-mini", "quality": {"status": "ok"}},
    "dimensions": {"dpi": 93, "width": 791, "height": 1023},
    "images": [
        {"id": "img-0.jpeg", "width_px": 1655, "height_px": 1159, "top_left_x": 138}
    ],
    "tables": [],
    "header": None,
}


def test_migration_maps_a_repaired_page():
    row = _migration().page_row(PAPER_ID, 1, LEGACY_PAGE)

    assert row["markdown"] == "repaired text"
    assert row["repair_markdown"] == "repaired text"
    assert row["ocr_markdown"] == "mistral text"
    assert row["markdown_source"] == "ocr_repair"
    assert row["text_layer"] == "layer text"
    assert row["ocr_quality"] == {
        "status": "suspect",
        "repair": {"model": "gpt-5.4-mini", "quality": {"status": "ok"}},
        "model": "gpt-5.4-mini",
    }
    assert set(row["ocr_payload"]) == {
        "index",
        "dimensions",
        "images",
        "tables",
        "header",
    }
    assert row["width_pt"] == round(791 * 72 / 93, 2)
    assert row["height_pt"] == round(1023 * 72 / 93, 2)


@pytest.mark.parametrize(
    "legacy,source",
    [(None, "ocr"), ("mistral", "ocr"), ("pymupdf_fallback", "text_layer")],
)
def test_migration_maps_markdown_sources(legacy, source):
    page = {"index": 0, "markdown": "text", "dimensions": {"dpi": 72}}
    if legacy:
        page["markdown_source"] = legacy
    row = _migration().page_row(PAPER_ID, 1, page)
    assert row["markdown_source"] == source
    assert row["ocr_markdown"] == "text"
    assert row["repair_markdown"] is None


def test_migration_converts_figure_boxes_with_the_page_dpi():
    figure = {
        "id": "img-0.jpeg",
        "dpi": 300,  # render DPI: must not be used for the box
        "page": 1,
        "label": "Figure 1",
        "caption": "cap",
        "s3_key": "figures/p/img-0.jpeg.png",
        "bbox": {
            "top_left_x": 93,
            "top_left_y": 186,
            "bottom_right_x": 279,
            "bottom_right_y": 465,
        },
    }
    row = _migration().figure_row(PAPER_ID, figure, [LEGACY_PAGE])

    assert row["bbox"] == {"x0": 72.0, "y0": 144.0, "x1": 216.0, "y1": 360.0}
    assert row["s3_key"] == "figures/p/img-0.jpeg.png"
    assert (row["page_no"], row["ocr_image_id"]) == (1, "img-0.jpeg")
    assert (row["width"], row["height"]) == (1655, 1159)
    assert (row["label"], row["caption"]) == ("Figure 1", "cap")
