"""Identifier extraction and title guesses for the metadata stages."""

import pymupdf

from app.ingest import metadata_ids as ids
from app.ingest.metadata_ids import Identifier


def values(found):
    return [(i.kind, i.value) for i in found]


# -- DOIs -----------------------------------------------------------------------


def test_doi_trailing_punctuation_is_dropped():
    assert ids.clean_doi("10.1038/s41586-021-03819-2.") == "10.1038/s41586-021-03819-2"
    assert ids.clean_doi("10.1000/ABC;") == "10.1000/abc"
    assert ids.clean_doi("10.1000/abc).") == "10.1000/abc"
    assert ids.clean_doi("10.1000/abc],") == "10.1000/abc"


def test_doi_keeps_balanced_brackets():
    # SICI-style DOIs carry their own parentheses.
    assert (
        ids.clean_doi("10.1002/(SICI)1097-4636(199706)35:4")
        == "10.1002/(sici)1097-4636(199706)35:4"
    )
    assert ids.clean_doi("10.1016/s0140-6736(20)30183-5)") == (
        "10.1016/s0140-6736(20)30183-5"
    )


def test_doi_forms_in_text():
    text = (
        "Cite as doi:10.1073/PNAS.2516511123. See https://doi.org/10.1038/nbt.2488, "
        "or (https://dx.doi.org/10.1186/1752-0509-7-74)."
    )
    assert values(ids.identifiers_in(text, "t")) == [
        ("doi", "10.1073/pnas.2516511123"),
        ("doi", "10.1038/nbt.2488"),
        ("doi", "10.1186/1752-0509-7-74"),
    ]


def test_doi_with_pdf_text_artifacts():
    # Real PNAS page-1 text: a backspace glued on, and a zero-width space +
    # line break inside the supplement URL.
    text = (
        "e2516511123\x08\nhttps://doi.org/10.1073/pnas.2516511123 1 of 12\n"
        "www.pnas.org/lookup/suppl/doi:10.1073/pnas.​\n2516511123/-\xad/DCS."
    )
    assert values(ids.identifiers_in(text, "t")) == [
        ("doi", "10.1073/pnas.2516511123"),
        ("doi", "10.1073/pnas.2516511123/-/dcs"),
    ]
    assert values(ids.identifiers_in("doi 10.1073/pnas.2516511123\x08", "t")) == [
        ("doi", "10.1073/pnas.2516511123")
    ]


def test_url_encoded_doi():
    found = ids.url_identifiers("https://doi.org/10.1002%2Fanie.201915678")
    assert values(found) == [("doi", "10.1002/anie.201915678")]
    assert values(ids.url_identifiers("https://arxiv.org/pdf/2404.15255v2")) == [
        ("arxiv", "2404.15255")
    ]


# -- arXiv ids --------------------------------------------------------------------


def test_arxiv_id_forms():
    text = (
        "arXiv:2404.15255v2 [cs.LG] 23 Apr 2024\n"
        "code at https://arxiv.org/abs/1706.03762 and arxiv.org/pdf/2106.09685v3\n"
        "old: arXiv:hep-th/9901001v1, arXiv: math.GT/0309136\n"
        "and DOI 10.48550/arXiv.2310.06825\n"
    )
    assert values(ids.identifiers_in(text, "t")) == [
        ("arxiv", "2404.15255"),
        ("arxiv", "1706.03762"),
        ("arxiv", "2106.09685"),
        ("arxiv", "hep-th/9901001"),
        ("arxiv", "math.GT/0309136"),
        ("arxiv", "2310.06825"),  # the arXiv DOI is reported as the id
    ]


def test_bare_numbers_are_not_arxiv_ids():
    text = "We ran 2404.15255 steps; table 1234.5678; the year 2023.1234."
    assert ids.identifiers_in(text, "t") == []


def test_invalid_month_is_not_an_arxiv_id():
    assert ids.identifiers_in("arXiv:2413.01234", "t") == []


# -- filenames / URLs -------------------------------------------------------------


def test_filename_identifiers():
    assert values(ids.filename_identifiers("2404.15255v2.pdf")) == [
        ("arxiv", "2404.15255")
    ]
    assert values(ids.filename_identifiers("arXiv_2106.09685.pdf")) == [
        ("arxiv", "2106.09685")
    ]
    assert values(ids.filename_identifiers("10.1038_s41586-021-03819-2.pdf")) == [
        ("doi", "10.1038/s41586-021-03819-2")
    ]
    # An S3 key is a UUID: nothing to find.
    assert ids.filename_identifiers("0bbbdd4d-5bbd-428b-99d3-3e61a6c2a273.pdf") == []
    assert ids.filename_identifiers(None) == []


# -- page ranking --------------------------------------------------------------


PAGE_1 = """\
Journal of Things 12 (2024) 1-10 https://doi.org/10.5555/header.2024.001
A Study of Things That Matter
Jane Doe, John Smith
Abstract
Prior work (Roe et al., doi:10.5555/cited.1999.042) looked at stuff.
1 Introduction
Things matter [1].
"""

PAGE_2 = """\
More text citing 10.5555/cited.2001.7 and arXiv:1706.03762.
References
[1] A. Roe. Old things. doi:10.5555/in.references.1
"""


def test_header_doi_ranks_before_body_and_page_2():
    ranked = ids.page_identifiers([PAGE_1, PAGE_2])
    assert values(ranked) == [
        ("doi", "10.5555/header.2024.001"),
        ("doi", "10.5555/cited.1999.042"),
        ("doi", "10.5555/cited.2001.7"),
        ("arxiv", "1706.03762"),
    ]
    assert ranked[0].where == "page 1 header"


def test_references_are_ignored():
    found = values(ids.page_identifiers([PAGE_1, PAGE_2]))
    assert ("doi", "10.5555/in.references.1") not in found


def test_arxiv_stamp_anywhere_on_page_1_ranks_before_body_dois():
    page = (
        "A Title of a Paper\nAuthors\nAbstract\nBody text doi:10.5555/cited.1\n"
        "arXiv:2404.15255v1 [cs.LG] 23 Apr 2024\n"
    )
    assert values(ids.page_identifiers([page])) == [
        ("arxiv", "2404.15255"),
        ("doi", "10.5555/cited.1"),
    ]


def test_unique_keeps_the_best_ranked():
    found = ids.unique(
        [
            Identifier("doi", "10.1/a", "embedded"),
            Identifier("doi", "10.1/A", "page 1"),
            Identifier("arxiv", "2404.15255", "page 1"),
        ]
    )
    assert [i.where for i in found] == ["embedded", "page 1"]


# -- titles and tokens ------------------------------------------------------------


def test_plausible_title_rejects_junk():
    assert ids.plausible_title("Microsoft Word - draft3.docx") is None
    assert ids.plausible_title("untitled") is None
    assert ids.plausible_title("main.tex") is None
    assert ids.plausible_title("12345") is None
    assert ids.plausible_title("Main effects of sleep on memory") == (
        "Main effects of sleep on memory"
    )


def test_title_from_text_skips_headers():
    assert ids.title_from_text(PAGE_1) == "A Study of Things That Matter"


def test_title_from_markdown_takes_the_first_real_heading():
    md = "# Journal of Things\n\n# A Study of Things That Matter\n\n## Abstract\n"
    assert ids.title_from_markdown(md) == "A Study of Things That Matter"


def test_tokens_fold_accents_ligatures_and_line_hyphens():
    assert ids.tokens("Hübotter’s ﬁne-tuning of repre-\nsentations") == [
        "hubotter",
        "s",
        "fine",
        "tuning",
        "of",
        "representations",
    ]


# -- the PDF's own metadata ------------------------------------------------------------


def make_pdf(metadata=None, xmp=None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Journal of Things 2024", fontsize=9)
    page.insert_text((72, 110), "A Study of Things", fontsize=20)
    page.insert_text((72, 135), "That Matter", fontsize=20)
    page.insert_text((72, 170), "Jane Doe and John Smith", fontsize=11)
    page.insert_text((72, 220), "Abstract. We study things.", fontsize=10)
    if metadata:
        doc.set_metadata(metadata)
    if xmp:
        doc.set_xml_metadata(xmp)
    return doc.tobytes()


def test_read_pdf_hints_layout_title_and_info():
    hints = ids.read_pdf_hints(
        make_pdf(
            metadata={
                "title": "Microsoft Word - draft3.docx",
                "subject": "Journal of Things, doi:10.5555/info.subject.1",
                "keywords": "things; matter, study",
            }
        )
    )
    assert hints.layout_title == "A Study of Things That Matter"
    assert hints.embedded_title is None  # junk title rejected
    assert values(hints.identifiers) == [("doi", "10.5555/info.subject.1")]
    assert hints.embedded_keywords == ["things", "matter", "study"]


def test_read_pdf_hints_xmp_doi():
    xmp = (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:prism="http://prismstandard.org/'
        'namespaces/basic/2.0/"><prism:doi>10.5555/XMP.2024.7</prism:doi>'
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    hints = ids.read_pdf_hints(make_pdf(metadata={"title": "A Study"}, xmp=xmp))
    assert values(hints.identifiers) == [("doi", "10.5555/xmp.2024.7")]
    assert hints.embedded_title is None  # too short to be a title
