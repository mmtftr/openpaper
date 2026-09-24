"""AI-highlight anchoring (`app.ingest.pdf.anchor`) on PDFs made in the test."""

import io

import pymupdf
import pytest

from app.ingest.pdf.anchor import (
    anchor_quotes,
    find_quote,
    normalize_quote,
    page_chars,
    scaled_position,
)
from app.schemas.highlight import ScaledPosition


def _lines_pdf(*pages: list[str], top: float = 100, leading: float = 14) -> bytes:
    """Pages of plain lines (Helvetica 11pt), baselines from `top` down."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()  # A4-ish default 595 x 842
        for i, line in enumerate(lines):
            page.insert_text((72, top + i * leading), line, fontsize=11)
    return doc.tobytes()


def _story(*paragraphs: str, width: float = 260) -> bytes:
    """One A4 page per paragraph, laid out (ligatures shaped) by MuPDF."""
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    for paragraph in paragraphs:
        story = pymupdf.Story(
            html=f"<p style='font-family:serif;font-size:14px'>{paragraph}</p>"
        )
        device = writer.begin_page(pymupdf.paper_rect("a4"))
        story.place(pymupdf.Rect(50, 50, 50 + width, 800))
        story.draw(device)
        writer.end_page()
    writer.close()
    return out.getvalue()


def _text_in(pdf: bytes, anchor: dict) -> str:
    """The PDF text whose char centres fall inside the anchor's rects
    (ligatures folded). Centres, since adjacent lines' boxes overlap."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[anchor["page_number"] - 1]
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
    lines = []
    for r in anchor["position"]["rects"]:
        chars = []
        for block in raw["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    for char in span["chars"]:
                        x0, y0, x1, y1 = char["bbox"]
                        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                        if r["x1"] <= cx <= r["x2"] and r["y1"] <= cy <= r["y2"]:
                            chars.append(char["c"])
        lines.append("".join(chars))
    return normalize_quote(" ".join(lines))


LIGATURES = (
    "The efficient final office workflow shows significant flow effects in the "
    "first figure of the paper we discuss here today."
)


def test_story_pdf_really_uses_ligature_glyphs():
    with pymupdf.open(stream=_story(LIGATURES), filetype="pdf") as doc:
        text = doc[0].get_text()
        assert "ﬃ" in text and "ﬁ" in text
        # pymupdf's own search can't see through them — why we don't use it.
        assert not doc[0].search_for("efficient final office")


def test_ligatures_are_anchored():
    pdf = _story(LIGATURES)
    [anchor] = anchor_quotes(pdf, ["efficient final office workflow"])
    assert anchor is not None and anchor["page_number"] == 1
    assert _text_in(pdf, anchor) == "efficient final office workflow"


def test_multi_line_quote_gets_one_rect_per_line_top_left_origin():
    pdf = _story(LIGATURES)
    quote = "office workflow shows significant flow effects in the first figure"
    [anchor] = anchor_quotes(pdf, [quote])
    assert anchor is not None
    position = anchor["position"]
    rects = position["rects"]
    assert len(rects) == 3  # the quote wraps over three lines
    # Top-left origin, y down: later lines sit lower (larger y), all near the
    # top of the page where the text was placed (not mirrored to the bottom).
    assert [r["y1"] for r in rects] == sorted(r["y1"] for r in rects)
    assert all(50 <= r["y1"] < r["y2"] < 200 for r in rects)
    assert all(r["width"] == 595 and r["height"] == 842 for r in rects)
    assert all(r["pageNumber"] == 1 for r in rects)
    box = position["boundingRect"]
    assert box["y1"] == rects[0]["y1"] and box["y2"] == rects[-1]["y2"]
    assert box["x1"] == min(r["x1"] for r in rects)
    assert _text_in(pdf, anchor) == quote

    # Stored as `ScaledPosition` JSON, with no `usePdfCoordinates` (which
    # would make the reader flip y).
    assert ScaledPosition.model_validate(position).to_json() == position
    assert "usePdfCoordinates" not in position


@pytest.mark.parametrize("drop_hyphen", [True, False])
def test_hyphenated_line_break(drop_hyphen):
    pdf = _lines_pdf(
        ["Our model achieves signifi-", "cant gains over the state-", "of-the-art."]
    )
    quote = (
        "achieves significant gains over the state-of-the-art"
        if drop_hyphen
        else "achieves signifi-cant gains"
    )
    [anchor] = anchor_quotes(pdf, [quote])
    assert anchor is not None
    rects = anchor["position"]["rects"]
    assert len(rects) == (3 if drop_hyphen else 2)
    # First baseline at y=100 (11pt): the first rect is around 90-103.
    assert 85 < rects[0]["y1"] < rects[0]["y2"] < 106


class _FakePage:
    """A page whose text layer ends split lines with U+00AD (PNAS does;
    pymupdf's own `insert_text` can't produce that)."""

    rect = pymupdf.Rect(0, 0, 600, 800)

    def get_text(self, kind, flags=0):
        def line(text, y):
            chars = [
                {"c": c, "bbox": (10 + 5 * i, y, 15 + 5 * i, y + 10)}
                for i, c in enumerate(text)
            ]
            return {"spans": [{"chars": chars}]}

        return {
            "blocks": [{"lines": [line("model and sta\xad", 100), line("bility", 112)]}]
        }


def test_soft_hyphen_line_break():
    page = page_chars(_FakePage(), 1)
    span = find_quote("model and stability", page)
    assert span is not None
    position = scaled_position(page, *span)
    assert position is not None
    assert [r["y1"] for r in position["rects"]] == [100, 112]


def test_markdown_and_latex_in_the_quote_are_normalised():
    pdf = _lines_pdf(
        ['Our method reduces error by 40% ("best" case) on x2 data.'],
    )
    quotes = [
        "**Our method** reduces error by 40% (“best” case) on $x^{2}$ data.",
    ]
    [anchor] = anchor_quotes(pdf, quotes)
    assert anchor is not None
    assert _text_in(pdf, anchor) == normalize_quote(quotes[0])


def test_section_number_period_and_whitespace_insensitive_matches():
    pdf = _lines_pdf(["3. Results and Discussion", "The A B C network wins."])
    section, spaced = anchor_quotes(
        pdf, ["3 Results and Discussion", "The ABC network wins."]
    )
    assert section is not None
    assert _text_in(pdf, section) == "3. Results and Discussion"
    assert spaced is not None


def test_first_page_containing_the_quote_wins_and_misses_are_none():
    pdf = _lines_pdf(
        ["An introduction to the topic."],
        ["The key result: accuracy doubles."],
    )
    found, missing, short = anchor_quotes(
        pdf, ["accuracy doubles", "never appears anywhere", "ab"]
    )
    assert found is not None and found["page_number"] == 2
    assert found["position"]["rects"][0]["pageNumber"] == 2
    assert missing is None and short is None
    assert anchor_quotes(pdf, []) == []
