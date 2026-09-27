"""`ingest.footnotes`: running headers/footers out of the page text, footnotes
as GFM footnotes (per page in `ocr`, paper-unique in `ocr_repair`, collected
at the end by the markdown API).

The two fixtures are real Mistral OCR responses (2026-09-27,
`extract_header` / `extract_footer` / `include_blocks`, image bitmaps
elided): pages 1–2 of arXiv 2602.08964 (ICML: affiliation note as a `text`
block at the bottom of the left column, running title header on page 2)
and pages 1 and 3 of arXiv 2406.11717 (NeurIPS: footnotes as `footer`
blocks). No network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.ingest import footnotes
from app.ingest.sources import mistral

FIXTURES = Path(__file__).parent / "fixtures" / "ingest_ocr"
REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
DEF_RE = re.compile(r"(?m)^\[\^([^\]\s]+)\]: (.*)$")


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def document(body: dict[str, Any], page_nos: list[int]) -> str:
    """What the markdown API serves: parse → renumber → collect."""
    pages = mistral.parse_pages(body, page_nos)
    return footnotes.collect_definitions(
        "\n\n".join(footnotes.renumber([p.markdown for p in pages]))
    )


# -- recorded responses ----------------------------------------------------------


def test_response_shape_header_footer_blocks():
    body = load("mistral_6d1f588b_p1-2.json")
    p1, p2 = body["pages"]
    assert (p1["header"], p1["footer"]) == (None, "1")
    assert p2["header"].startswith("A Behavioural and Representational")
    assert p2["footer"] == "2"
    # The header/footer are not in Mistral's markdown any more ...
    assert p2["header"] not in p2["markdown"]
    # ... but the affiliation note is: Mistral calls it a plain text block.
    kinds = {b["type"] for b in p1["blocks"]}
    assert "footnote" not in kinds and {"text", "footer", "image"} <= kinds
    assert "*Alphabetical order" in p1["markdown"]

    ref = load("mistral_bf37c3a1_p1_p3.json")["pages"][1]
    # Footnotes come back as footer blocks, joined into `footer`.
    footer_blocks = [b["content"] for b in ref["blocks"] if b["type"] == "footer"]
    assert ref["footer"] == "\n".join(footer_blocks)
    assert "We shorten" in ref["footer"] and "We shorten" not in ref["markdown"]


def test_icml_affiliation_note_becomes_footnotes():
    body = load("mistral_6d1f588b_p1-2.json")
    raw = body["pages"][0]["markdown"]
    p1, p2 = mistral.parse_pages(body, [1, 2])

    text, _, notes = p1.markdown.partition("\n\n[^")
    notes = "[^" + notes
    # The note area (affiliations + the ICML "Proceedings ..." line) left
    # the body; everything else is Mistral's markdown, unchanged.
    assert "Alphabetical order" not in text and "Proceedings of the 43" not in text
    affiliation = next(
        b["content"]
        for b in body["pages"][0]["blocks"]
        if b["content"].startswith("*Alphabetical")
    )
    proceedings = next(
        b["content"]
        for b in body["pages"][0]["blocks"]
        if b["content"].startswith("Proceedings")
    )
    expected = raw.replace(affiliation + "\n\n", "").replace(proceedings + "\n\n", "")
    assert REF_RE.sub("", text) == re.sub(r"\\\( \^\{\d?\*?\} \\\)", "", expected)

    # Author markers point at the split affiliation notes ("1*" → two refs).
    assert "Raghu Arghal [^2][^1]" in text
    assert "Gabriele Sarti [^9]" in text
    assert "Mario Giulianelli [^4]" in text
    assert dict(DEF_RE.findall(notes)) == {
        "1": "Alphabetical order; see Statement of Author Contributions.",
        "2": "University of Pennsylvania",
        "3": "New York University",
        "4": "University College London",
        "5": "Fraunhofer HHI",
        "6": "Indiana University, Bloomington",
        "7": "TKH AI",
        "8": "Independent",
        "9": "Northeastern University. Correspondence to: Mario Giulianelli "
        "<m.giulianelli@ucl.ac.uk>.",
        "10": "Proceedings of the 43 \\( ^{rd} \\) International Conference on "
        "Machine Learning, Seoul, South Korea. PMLR 306, 2026. Copyright 2026 "
        "by the author(s).",
    }
    # Figures are unaffected.
    assert "![img-0.jpeg](img-0.jpeg)" in p1.markdown
    assert [f.label for f in p1.figures] == ["Figure 1"]
    # Page 2: the running header and page number are only in the payload.
    assert p2.markdown == body["pages"][1]["markdown"]
    assert p2.payload["header"].startswith("A Behavioural")
    assert p2.payload["footer"] == "2"


def test_neurips_footer_footnotes():
    body = load("mistral_bf37c3a1_p1_p3.json")
    p1, p3 = mistral.parse_pages(body, [1, 3])

    assert "Oscar Obeso [^1]" in p1.markdown
    assert "controlling model behavior. [^2]" in p1.markdown
    assert DEF_RE.findall(p1.markdown) == [
        ("1", "Correspondence to andyrdt@gmail.com, obalcells@student.ethz.ch."),
        ("2", "Code available at https://github.com/andyrdt/refusal_direction."),
        # No marker: still a note, unreferenced.
        (
            "3",
            "38th Conference on Neural Information Processing Systems (NeurIPS 2024).",
        ),
    ]
    # Only footnote 3's marker survived OCR on page 3; 1 and 2 stay notes.
    assert "specified in Table 1. [^3]" in p3.markdown
    assert [label for label, _ in DEF_RE.findall(p3.markdown)] == ["1", "2", "3"]
    assert not re.search(r"(?m)^3$", p3.markdown)  # the page number
    # Math and tables untouched.
    raw3 = body["pages"][1]["markdown"]
    for chunk in (r"\mathbf{x}_i^{(1)}", "|  QWEN CHAT | 1.8B, 7B, 14B, 72B | AFT"):
        assert chunk in raw3 and chunk in p3.markdown


def test_document_has_unique_footnotes_at_the_end():
    markdown = document(load("mistral_bf37c3a1_p1_p3.json"), [1, 3])
    defs = DEF_RE.findall(markdown)
    labels = [label for label, _ in defs]
    assert labels == [str(n) for n in range(1, 7)]
    # All definitions come after all the text.
    first_def = markdown.index("\n[^1]: ")
    assert not DEF_RE.search(markdown[:first_def])
    # Every reference points at a definition; refs in first-appearance order.
    refs = REF_RE.findall(markdown[:first_def])
    assert refs == ["1", "2", "4"] and set(refs) <= set(labels)
    assert dict(defs)["4"].startswith("Unless explicitly stated otherwise")

    icml = document(load("mistral_6d1f588b_p1-2.json"), [1, 2])
    assert "A Behavioural and Representational Evaluation" not in icml.split("\n", 3)[3]
    assert icml.rstrip().endswith("Copyright 2026 by the author(s).")


# -- units -------------------------------------------------------------------------


def page(
    *,
    footer: str | None = None,
    header: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "header": header,
        "footer": footer,
        "blocks": blocks or [],
        "dimensions": {"dpi": 93, "height": 1000, "width": 800},
    }


def block(kind: str, content: str, y0: int, y1: int, x0: int = 60, x1: int = 380):
    return {
        "type": kind,
        "content": content,
        "top_left_x": x0,
        "top_left_y": y0,
        "bottom_right_x": x1,
        "bottom_right_y": y1,
    }


def test_page_without_footnotes_is_unchanged():
    md = "# Title\n\nText with \\( x^{2} \\) and a table.\n\n| a | b |\n| --- | --- |"
    assert footnotes.split_page(md, page()) == md
    assert footnotes.split_page(md, page(footer="12")) == md
    assert footnotes.renumber([md, md]) == [md, md]
    assert footnotes.collect_definitions(md) == md


@pytest.mark.parametrize(
    ("ref", "note"),
    [
        ("\\( ^{1} \\)", "\\( ^{1} \\) The note."),
        ("${}^{1}$", "${}^{1}$ The note."),
        ("$^{1}$", "$^{1}$The note."),
        ("¹", "¹ The note."),
        ("<sup>1</sup>", "<sup>1</sup> The note."),
        ("\\( ^{\\dagger} \\)", "† The note."),
        ("\\( ^{*} \\)", "*The note."),
    ],
)
def test_marker_forms_link(ref, note):
    md = f"A claim.{ref} More text."
    out = footnotes.split_page(md, page(footer=note))
    assert out == "A claim.[^1] More text.\n\n[^1]: The note."


def test_exponents_are_not_references():
    md = "Speed 10 \\( ^{1} \\) m/s and \\( x \\) \\( ^{1} \\) and word \\( ^{1} \\)."
    out = footnotes.split_page(md, page(footer="\\( ^{1} \\) Note."))
    assert out.startswith(
        "Speed 10 \\( ^{1} \\) m/s and \\( x \\) \\( ^{1} \\) and word [^1]."
    )


def test_footer_left_in_markdown_is_removed():
    """Older responses (no extract_footer) keep header/footer in the text."""
    md = "Running Title\n\nBody text.\n\n\\( ^{1} \\) A note.\n\n7"
    out = footnotes.split_page(
        md, page(header="Running Title", footer="\\( ^{1} \\) A note.\n7")
    )
    assert out == "Body text.\n\n[^1]: A note."


def test_note_area_needs_the_bottom_of_its_column():
    body_text = "Body paragraph that continues below the marker block."
    marker_block = "\\( ^{1} \\) Looks like a note."
    md = f"Intro.\n\n{marker_block}\n\n{body_text}"
    blocks = [
        block("text", "Intro.", 100, 500),
        block("text", marker_block, 700, 720),
        block("text", body_text, 800, 900),  # same column, well below
    ]
    assert footnotes.split_page(md, page(blocks=blocks)) == md
    # In the other column, it doesn't matter.
    blocks[2] = block("text", body_text, 800, 900, x0=420, x1=740)
    assert footnotes.split_page(md, page(blocks=blocks)) == (
        f"Intro.\n\n{body_text}\n\n[^1]: Looks like a note."
    )
    # High on the page, a marker-led paragraph is body text.
    blocks[1] = block("text", marker_block, 300, 320)
    assert footnotes.split_page(md, page(blocks=blocks)) == md


def test_emphasis_is_not_a_marker():
    md = "Text.\n\n*Italic remark* at the bottom."
    blocks = [block("text", "*Italic remark* at the bottom.", 900, 920)]
    assert footnotes.split_page(md, page(blocks=blocks)) == md


def test_renumber_drops_running_footers_and_keeps_code():
    running = "[^1]: Nature Communications | (2026)17:5926"
    pages = [
        "Page one[^1].\n\n[^1]: Real note.\n\n[^2]: Nature Communications | (2026)17:5926",
        f"Page two with `re.sub(r'[^a-z]', '', s)`.\n\n{running}",
        "Page three[^1].\n\n[^1]: Another note.\n\n[^2]: Preprint.",
    ]
    assert footnotes.renumber(pages) == [
        "Page one[^1].\n\n[^1]: Real note.",
        "Page two with `re.sub(r'[^a-z]', '', s)`.",
        "Page three[^2].\n\n[^2]: Another note.\n\n[^3]: Preprint.",
    ]


def test_collect_definitions_moves_notes_to_the_end():
    md = "One[^1].\n\n[^1]: First.\n\nTwo[^2].\n\n[^2]: Second.\n\nThree."
    assert footnotes.collect_definitions(md) == (
        "One[^1].\n\nTwo[^2].\n\nThree.\n\n[^1]: First.\n\n[^2]: Second."
    )
