"""`read_pages` truncates on page boundaries and reports where it stopped.

The cap used to hard-cut the joined markdown mid-page and report nothing but
`truncated: true`, so the model could not tell which pages it actually got or
where to resume. These tests pin the page-boundary behaviour for both parsers
(the structured Mistral pages and the pymupdf `page_offset_map` fallback).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.llm.tools import section_tools
from app.llm.tools.section_tools import RESPONSE_CHAR_CAP, read_pages

PAPER_ID = "11111111-1111-1111-1111-111111111111"
USER = SimpleNamespace(id="22222222-2222-2222-2222-222222222222")

SEP = section_tools.PAGE_SEPARATOR


def _mistral_paper(page_texts: List[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=PAPER_ID,
        parser="mistral",
        ocr={
            "pages": [
                {"index": i, "markdown": md} for i, md in enumerate(page_texts)
            ]
        },
        raw_content="",
        page_offset_map=None,
    )


def _pymupdf_paper(
    page_texts: List[str], *, str_keys: bool = False
) -> SimpleNamespace:
    """Flat raw_content plus the 1-indexed offset map, as pymupdf papers store it."""
    offsets: Dict[Any, List[int]] = {}
    cursor = 0
    for i, md in enumerate(page_texts):
        key: Any = str(i + 1) if str_keys else i + 1
        offsets[key] = [cursor, cursor + len(md)]
        cursor += len(md)
    return SimpleNamespace(
        id=PAPER_ID,
        parser="pymupdf",
        ocr=None,
        raw_content="".join(page_texts),
        page_offset_map=offsets,
    )


@pytest.fixture
def use_paper(monkeypatch):
    """Resolve every lookup to the given fake paper (the tools go via paper_crud)."""

    def _install(paper: Any) -> Any:
        monkeypatch.setattr(section_tools.paper_crud, "get", lambda *a, **k: paper)
        return paper

    return _install


def _read(start: int, end: int) -> Dict[str, Any]:
    return read_pages(
        paper_id=PAPER_ID, start=start, end=end, current_user=USER, db=None
    )


# Three pages of 15k: pages 1+2 (+ separator) fit under the 32k cap, page 3
# does not — the interesting boundary case without building a 100k fixture.
THIRD_OF_CAP = 15_000


def _pages(*sizes: int) -> List[str]:
    return [chr(ord("a") + i) * size for i, size in enumerate(sizes)]


# =====================================================================
# Mistral (structured per-page markdown)
# =====================================================================


def test_mistral_range_that_fits_is_not_truncated(use_paper):
    pages = _pages(100, 200, 300)
    use_paper(_mistral_paper(pages))

    result = _read(1, 3)

    assert result["content"] == SEP.join(pages)
    assert result["pages"] == [1, 3]
    assert result["pages_returned"] == [1, 3]
    assert "truncated" not in result
    assert "next_page" not in result


def test_mistral_stops_on_a_page_boundary_and_points_at_the_next_page(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_mistral_paper(pages))

    result = _read(1, 3)

    # Exactly pages 1..2, joined the same way an untruncated read joins them.
    assert result["content"] == pages[0] + SEP + pages[1]
    assert result["pages"] == [1, 3]
    assert result["pages_returned"] == [1, 2]
    assert result["truncated"] is True
    assert result["next_page"] == 3
    assert "page_truncated" not in result
    assert len(result["content"]) <= RESPONSE_CHAR_CAP


def test_mistral_single_oversized_page_is_hard_truncated(use_paper):
    pages = _pages(RESPONSE_CHAR_CAP + 5_000, 100)
    use_paper(_mistral_paper(pages))

    result = _read(1, 2)

    assert result["content"] == pages[0][:RESPONSE_CHAR_CAP]
    assert result["pages_returned"] == [1, 1]
    assert result["truncated"] is True
    assert result["page_truncated"] is True
    # There is no clean boundary to resume from inside a single page.
    assert "next_page" not in result


def test_mistral_partial_range_starts_where_asked(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_mistral_paper(pages))

    result = _read(2, 4)

    assert result["content"] == pages[1] + SEP + pages[2]
    assert result["pages_returned"] == [2, 3]
    assert result["next_page"] == 4


def test_mistral_range_past_the_last_page_is_not_flagged_truncated(use_paper):
    pages = _pages(100, 100, 100)
    use_paper(_mistral_paper(pages))

    result = _read(2, 99)

    assert result["content"] == pages[1] + SEP + pages[2]
    assert result["pages_returned"] == [2, 3]
    # Nothing was cut by the cap — the paper simply ended.
    assert "truncated" not in result
    assert "next_page" not in result


def test_mistral_range_with_no_pages_is_an_explicit_error(use_paper):
    use_paper(_mistral_paper(_pages(100, 100)))

    result = _read(50, 60)

    assert "error" in result
    assert "content" not in result


# =====================================================================
# pymupdf (flat raw_content + page_offset_map)
# =====================================================================


def test_pymupdf_range_that_fits_returns_the_continuous_slice(use_paper):
    pages = _pages(100, 200, 300)
    paper = use_paper(_pymupdf_paper(pages))

    result = _read(1, 3)

    assert result["content"] == paper.raw_content
    assert result["pages_returned"] == [1, 3]
    assert "truncated" not in result


def test_pymupdf_stops_on_a_page_boundary(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_pymupdf_paper(pages))

    result = _read(1, 3)

    assert result["content"] == pages[0] + pages[1]
    assert result["pages_returned"] == [1, 2]
    assert result["truncated"] is True
    assert result["next_page"] == 3


def test_pymupdf_stringified_offset_keys_work_the_same(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_pymupdf_paper(pages, str_keys=True))

    result = _read(1, 3)

    assert result["pages_returned"] == [1, 2]
    assert result["next_page"] == 3


def test_pymupdf_single_oversized_page_is_hard_truncated(use_paper):
    pages = _pages(RESPONSE_CHAR_CAP + 5_000, 100)
    use_paper(_pymupdf_paper(pages))

    result = _read(1, 2)

    assert result["content"] == pages[0][:RESPONSE_CHAR_CAP]
    assert result["pages_returned"] == [1, 1]
    assert result["page_truncated"] is True
    assert "next_page" not in result


def test_pymupdf_end_past_the_last_page_returns_what_exists(use_paper):
    pages = _pages(100, 100)
    paper = use_paper(_pymupdf_paper(pages))

    result = _read(1, 10_000)

    assert result["content"] == paper.raw_content
    assert result["pages_returned"] == [1, 2]
    assert "truncated" not in result


def test_pymupdf_missing_start_page_errors(use_paper):
    use_paper(_pymupdf_paper(_pages(100, 100)))

    assert "error" in _read(7, 9)


def test_pymupdf_without_an_offset_map_errors(use_paper):
    paper = _pymupdf_paper(_pages(100))
    paper.page_offset_map = {}
    use_paper(paper)

    assert "error" in _read(1, 1)


# =====================================================================
# Shared validation
# =====================================================================


@pytest.mark.parametrize("start,end", [(0, 3), (-2, 1), (5, 4)])
@pytest.mark.parametrize("builder", [_mistral_paper, _pymupdf_paper])
def test_invalid_ranges_are_rejected(use_paper, builder, start, end):
    use_paper(builder(_pages(100, 100)))

    assert _read(start, end) == {"error": "Invalid page range"}


def test_pymupdf_malformed_offset_entry_is_treated_as_missing(use_paper):
    paper = _pymupdf_paper(_pages(100, 100))
    paper.page_offset_map[2] = "garbage"  # jsonb can hold anything
    use_paper(paper)

    result = _read(1, 2)

    # Page 1 still comes back; the unusable entry is skipped, not crashed on.
    assert result["pages_returned"] == [1, 1]
    assert "truncated" not in result
