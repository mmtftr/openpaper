"""
End-to-end check on the citation reconciliation pipeline using one of the
backfilled papers.

For each kind of candidate (prose / heading / table / math), pick a quote
from the OCR markdown and run it through the full reconciler. Reports
which path resolved each one (normalizer / llm / unmatched).

Run: docker compose exec server python -m app.scripts.test_reconcile_pipeline
"""

import asyncio
import logging
import os
import re
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from app.database.database import SessionLocal
from app.database.models import Paper
from app.llm.citation_normalizer import find_in_pdf_text
from app.llm.operations import operations
from app.llm.chat.citations import _reconcile_one_via_llm

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


CANDIDATE_PATTERNS = [
    ("prose", re.compile(r"^[A-Z][^\n]{60,200}\.$", re.MULTILINE)),
    ("heading", re.compile(r"^#{2,4}\s+[^\n]{10,80}$", re.MULTILINE)),
    ("bold", re.compile(r"\*\*([^*\n]{20,200})\*\*")),
    ("inline_math", re.compile(r"\$[^$\n]{2,100}\$")),
    ("table_row", re.compile(r"^\|[^\n]+\|[^\n]+\|$", re.MULTILINE)),
]


async def run() -> None:
    db = SessionLocal()
    try:
        paper = (
            db.query(Paper)
            .filter(Paper.parser == "mistral")
            .filter(Paper.s3_object_key.isnot(None))
            .first()
        )
        if not paper:
            print("no mistral-parsed papers")
            return

        ocr = paper.ocr or {}
        pages = ocr.get("pages") or []
        print(f"\nUsing paper: {paper.title}")
        print(f"  pages: {len(pages)}")

        norm_hits = 0
        llm_hits = 0
        misses = 0
        details: List[Dict[str, Any]] = []

        # Pick one candidate per kind from across all pages.
        for kind, pat in CANDIDATE_PATTERNS:
            for page_dict in pages:
                idx = page_dict.get("index")
                page_num = (int(idx) + 1) if idx is not None else None
                md = page_dict.get("markdown") or ""
                pymupdf_txt = page_dict.get("pymupdf_text") or ""
                if not pymupdf_txt:
                    continue
                m = pat.search(md)
                if not m:
                    continue
                quote = m.group(1) if m.groups() else m.group(0)
                quote = quote.strip().strip('"').strip("'")
                if len(quote) < 8:
                    continue

                # Path 1: normalizer
                normalized = find_in_pdf_text(quote, pymupdf_txt)
                if normalized:
                    norm_hits += 1
                    details.append(
                        {
                            "kind": kind,
                            "page": page_num,
                            "via": "normalizer",
                            "quote": quote[:80],
                            "matched": normalized[:80],
                        }
                    )
                    break

                # Path 2: LLM
                citation = {"reference": quote, "page": page_num}
                via_llm = await _reconcile_one_via_llm(
                    citation, pymupdf_txt, operations
                )
                if via_llm:
                    llm_hits += 1
                    details.append(
                        {
                            "kind": kind,
                            "page": page_num,
                            "via": "llm",
                            "quote": quote[:80],
                            "matched": via_llm[:80],
                        }
                    )
                else:
                    misses += 1
                    details.append(
                        {
                            "kind": kind,
                            "page": page_num,
                            "via": "miss",
                            "quote": quote[:80],
                            "matched": "",
                        }
                    )
                break

        print()
        print(f"Normalizer hits: {norm_hits}")
        print(f"LLM hits:        {llm_hits}")
        print(f"Misses:          {misses}")
        print()
        for d in details:
            print(f"[{d['kind']}] (p.{d['page']}) via={d['via']}")
            print(f"  quote:   {d['quote']}")
            print(f"  matched: {d['matched']}")
            print()
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
