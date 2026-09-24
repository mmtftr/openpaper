"""`read_pages` truncates on page boundaries and reports where it stopped.

The cap used to hard-cut the joined markdown mid-page and report nothing but
`truncated: true`, so the model could not tell which pages it actually got or
where to resume. These tests pin the page-boundary behaviour over the
per-page markdown in `paper_pages`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.ingest.content import Page
from app.llm.tools import section_tools
from app.llm.tools.section_tools import RESPONSE_CHAR_CAP, read_pages

PAPER_ID = "11111111-1111-1111-1111-111111111111"
USER = SimpleNamespace(id="22222222-2222-2222-2222-222222222222")

SEP = section_tools.PAGE_SEPARATOR


def _paper(page_texts: List[str]) -> SimpleNamespace:
    """A fake paper plus its `paper_pages` markdown, one entry per page."""
    return SimpleNamespace(
        id=PAPER_ID,
        pages=[Page(page_no=i + 1, markdown=md) for i, md in enumerate(page_texts)],
    )


@pytest.fixture
def use_paper(monkeypatch):
    """Resolve every lookup to the given fake paper (the tools go via
    paper_crud) and its pages (via `content.pages`)."""

    def _install(paper: Any) -> Any:
        monkeypatch.setattr(section_tools.paper_crud, "get", lambda *a, **k: paper)
        monkeypatch.setattr(section_tools.content, "pages", lambda db, pid: paper.pages)
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
# Per-page markdown (`paper_pages`)
# =====================================================================


def test_range_that_fits_is_not_truncated(use_paper):
    pages = _pages(100, 200, 300)
    use_paper(_paper(pages))

    result = _read(1, 3)

    assert result["content"] == SEP.join(pages)
    assert result["pages"] == [1, 3]
    assert result["pages_returned"] == [1, 3]
    assert "truncated" not in result
    assert "next_page" not in result


def test_stops_on_a_page_boundary_and_points_at_the_next_page(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_paper(pages))

    result = _read(1, 3)

    # Exactly pages 1..2, joined the same way an untruncated read joins them.
    assert result["content"] == pages[0] + SEP + pages[1]
    assert result["pages"] == [1, 3]
    assert result["pages_returned"] == [1, 2]
    assert result["truncated"] is True
    assert result["next_page"] == 3
    assert "page_truncated" not in result
    assert len(result["content"]) <= RESPONSE_CHAR_CAP


def test_single_oversized_page_is_hard_truncated(use_paper):
    pages = _pages(RESPONSE_CHAR_CAP + 5_000, 100)
    use_paper(_paper(pages))

    result = _read(1, 2)

    assert result["content"] == pages[0][:RESPONSE_CHAR_CAP]
    assert result["pages_returned"] == [1, 1]
    assert result["truncated"] is True
    assert result["page_truncated"] is True
    # There is no clean boundary to resume from inside a single page.
    assert "next_page" not in result


def test_partial_range_starts_where_asked(use_paper):
    pages = _pages(THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP, THIRD_OF_CAP)
    use_paper(_paper(pages))

    result = _read(2, 4)

    assert result["content"] == pages[1] + SEP + pages[2]
    assert result["pages_returned"] == [2, 3]
    assert result["next_page"] == 4


def test_range_past_the_last_page_is_not_flagged_truncated(use_paper):
    pages = _pages(100, 100, 100)
    use_paper(_paper(pages))

    result = _read(2, 99)

    assert result["content"] == pages[1] + SEP + pages[2]
    assert result["pages_returned"] == [2, 3]
    # Nothing was cut by the cap — the paper simply ended.
    assert "truncated" not in result
    assert "next_page" not in result


def test_range_with_no_pages_is_an_explicit_error(use_paper):
    use_paper(_paper(_pages(100, 100)))

    result = _read(50, 60)

    assert "error" in result
    assert "content" not in result


# =====================================================================
# Shared validation
# =====================================================================


@pytest.mark.parametrize("start,end", [(0, 3), (-2, 1), (5, 4)])
def test_invalid_ranges_are_rejected(use_paper, start, end):
    use_paper(_paper(_pages(100, 100)))

    assert _read(start, end) == {"error": "Invalid page range"}
