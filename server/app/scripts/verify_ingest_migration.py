"""Check the `ingest_v2_data_20260927` migration (docs/INGEST_DESIGN.md §10).

Read-only. Two steps around `alembic upgrade head`:

    python -m app.scripts.verify_ingest_migration snapshot before.json
    alembic upgrade head
    python -m app.scripts.verify_ingest_migration check before.json

`snapshot` records per-paper highlight / annotation / conversation / message
counts. `check` compares them (papers deleted by the migration must be the
ones without OCR) and verifies, for every paper with a legacy `ocr` object:
page count = `page_count` = `len(ocr.pages)`, figure count =
`len(ocr.figures)`, the pages' markdown joined = `raw_content` (so chat,
search and highlight offsets see the same text), `ts_vector` = the old
title + raw_content vector, the stage rows, and the AI highlights' / annotations' origin.
Exits 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database.database import engine

EXPECTED_MAIN = {
    "source": "succeeded",
    "text_layer": "succeeded",
    "preview": "succeeded",
    "ocr": "succeeded",
    "figures": "succeeded",
    "ocr_repair": "succeeded",
    "metadata": "succeeded",
    "metadata_fallback": "skipped",
    "highlights": "succeeded",
}
SUPPLEMENTARY = {"source", "text_layer", "preview", "ocr", "figures", "ocr_repair"}

COUNTS_SQL = """
SELECT p.id::text,
       (SELECT count(*) FROM highlights h WHERE h.paper_id = p.id),
       (SELECT count(*) FROM annotations a WHERE a.paper_id = p.id),
       (SELECT count(*) FROM conversations c WHERE c.conversable_id = p.id),
       (SELECT count(*) FROM messages m JOIN conversations c
            ON c.id = m.conversation_id WHERE c.conversable_id = p.id),
       jsonb_typeof(p.ocr) = 'object'
FROM papers p
"""


def counts(conn: Connection) -> dict[str, dict[str, Any]]:
    return {
        pid: {
            "highlights": h,
            "annotations": a,
            "conversations": c,
            "messages": m,
            "has_ocr": bool(has_ocr),
        }
        for pid, h, a, c, m, has_ocr in conn.execute(text(COUNTS_SQL))
    }


PAPER_CHECKS_SQL = """
SELECT p.id::text,
       p.supplementary_of_paper_id IS NOT NULL,
       p.page_count,
       jsonb_array_length(p.ocr->'pages'),
       jsonb_array_length(coalesce(p.ocr->'figures', '[]'::jsonb)),
       (SELECT count(*) FROM paper_pages pp WHERE pp.paper_id = p.id),
       (SELECT count(*) FROM paper_figures pf WHERE pf.paper_id = p.id),
       (SELECT count(*) FROM paper_figures pf WHERE pf.paper_id = p.id
            AND pf.s3_key IS DISTINCT FROM (
                SELECT f->>'s3_key' FROM jsonb_array_elements(p.ocr->'figures') f
                WHERE f->>'id' = pf.ocr_image_id AND (f->>'page')::int = pf.page_no)),
       coalesce((SELECT string_agg(markdown, E'\\n\\n' ORDER BY page_no)
                 FROM paper_pages pp WHERE pp.paper_id = p.id), '')
           = coalesce(p.raw_content, ''),
       p.ts_vector = setweight(to_tsvector('pg_catalog.english', coalesce(p.title, '')), 'A')
           || setweight(to_tsvector('pg_catalog.english', coalesce(p.raw_content, '')), 'D'),
       p.generated_outline IS NOT NULL,
       (SELECT jsonb_object_agg(s.name, s.status) FROM ingest_stages s
            WHERE s.paper_id = p.id)
FROM papers p
WHERE jsonb_typeof(p.ocr) = 'object'
ORDER BY p.created_at
"""


def check_papers(conn: Connection) -> list[str]:
    problems: list[str] = []
    rows = conn.execute(text(PAPER_CHECKS_SQL)).all()
    for (
        pid,
        is_supp,
        page_count,
        ocr_pages,
        ocr_figures,
        pages,
        figures,
        figure_key_mismatches,
        same_text,
        same_vector,
        has_outline,
        stages,
    ) in rows:
        if not (pages == ocr_pages == (page_count or ocr_pages)):
            problems.append(
                f"{pid}: pages {pages}, ocr.pages {ocr_pages}, page_count {page_count}"
            )
        if figures != ocr_figures:
            problems.append(f"{pid}: figures {figures}, ocr.figures {ocr_figures}")
        if figure_key_mismatches:
            problems.append(f"{pid}: {figure_key_mismatches} figure s3_keys differ")
        if not same_text:
            problems.append(f"{pid}: pages' markdown != raw_content")
        if not same_vector:
            problems.append(f"{pid}: ts_vector differs from title + raw_content")
        expected = (
            {n: EXPECTED_MAIN[n] for n in SUPPLEMENTARY}
            if is_supp
            else dict(EXPECTED_MAIN)
        )
        expected["outline"] = "succeeded" if has_outline else "queued"
        if (stages or {}) != expected:
            problems.append(f"{pid}: stages {stages} != {expected}")
    print(f"checked {len(rows)} papers with OCR")
    return problems


def check_origins(conn: Connection) -> list[str]:
    problems: list[str] = []
    for table in ("highlights", "annotations"):
        wrong = conn.execute(
            text(
                f"SELECT count(*) FROM {table}"
                " WHERE (role = 'assistant') <> (origin = 'ai')"
            )
        ).scalar_one()
        if wrong:
            problems.append(f"{wrong} {table} whose origin doesn't match role")
    return problems


def check_counts(before: dict[str, dict[str, Any]], after: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for pid, old in before.items():
        new = after.get(pid)
        if new is None:
            if old["has_ocr"]:
                problems.append(f"{pid}: deleted but it had OCR")
            else:
                print(f"{pid}: deleted (no OCR): {old}")
            continue
        for key in ("highlights", "annotations", "conversations", "messages"):
            if new[key] != old[key]:
                problems.append(f"{pid}: {key} {old[key]} -> {new[key]}")
    for pid in after.keys() - before.keys():
        problems.append(f"{pid}: new paper appeared")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the ingest v2 data migration.")
    parser.add_argument("step", choices=["snapshot", "check"])
    parser.add_argument("baseline", help="JSON file with the pre-migration counts")
    args = parser.parse_args()

    with engine.connect() as conn:
        if args.step == "snapshot":
            with open(args.baseline, "w") as f:
                json.dump(counts(conn), f, indent=1)
            print(f"wrote {args.baseline}")
            return 0

        with open(args.baseline) as f:
            before = json.load(f)
        problems = (
            check_counts(before, counts(conn))
            + check_papers(conn)
            + check_origins(conn)
        )
        totals = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM papers), (SELECT count(*) FROM paper_pages),"
                " (SELECT count(*) FROM paper_figures),"
                " (SELECT count(*) FROM ingest_stages),"
                " (SELECT count(*) FROM ingest_stages WHERE status = 'queued'),"
                " (SELECT count(*) FROM highlights WHERE origin = 'ai')"
            )
        ).one()
    print(
        "papers={} pages={} figures={} stage_rows={} queued={} ai_highlights={}".format(
            *totals
        )
    )
    for problem in problems:
        print("MISMATCH", problem)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
