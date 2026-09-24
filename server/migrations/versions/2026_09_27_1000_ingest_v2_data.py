"""ingest v2 data: existing papers → paper_pages / paper_figures / stages.

docs/INGEST_DESIGN.md §10. No OCR or LLM calls — everything is copied from
what the old pipeline stored in `papers.ocr`:

1. Papers without a Mistral OCR object (`ocr` not a JSON object) and not yet
   ingested by v2 are deleted (one live paper, pymupdf-only). Refuses to
   run if such a paper has conversations or is in a project.
2. `ocr.pages[i]` → `paper_pages` (page_no = i + 1):
   `markdown` = the final text; `ocr_markdown` = Mistral's text
   (`mistral_markdown` on repaired pages, whose `markdown` was replaced);
   `repair_markdown` only for `openai_ocr_repair` pages; `text_layer` =
   `pymupdf_text`; `markdown_source` 'mistral'/missing → ocr,
   'openai_ocr_repair' → ocr_repair, 'pymupdf_fallback' → text_layer;
   `ocr_quality` as stored (+ `repair` / `model` from `openai_ocr`, the
   shape the ocr_repair stage writes); `ocr_payload` = the page object
   minus the text and legacy repair keys; page size in points from the
   OCR `dimensions` (px * 72 / dpi).
   `ocr.figures[]` → `paper_figures`: `page` is already 1-based; the bbox
   (OCR-image px, top-left) → PDF points with that page's dpi (`figure.dpi`
   is the render DPI, not the OCR one); `s3_key`, label and caption as
   stored — saved chat history refers to those keys.
3. `ingest_stages`: succeeded for every stage whose output exists,
   `metadata_fallback` skipped, `preview` / `outline` queued where missing
   (`outline` runs on the next worker start: no generated outline yet).
4. `highlights.origin = 'ai'` for the old pipeline's AI highlights
   (`role = 'assistant'`); same for `annotations.origin`, which is added
   here (the model has it, the schema migration didn't create it).
5. The search vector: `papers.ts_vector` = title (weight A) + the pages'
   markdown joined by a blank line (weight D) — the same text `raw_content`
   held. Recomputed when the title changes and, once per paper per
   transaction, when a page's `markdown` changes (a deferred trigger, so
   ocr_repair's per-page updates cost one recompute, not one per page).

Legacy columns (`ocr`, `raw_content`, `page_offset_map`, `parser`) stay until
the cutover is done. Check the result with
`python -m app.scripts.verify_ingest_migration`.

Revision ID: ingest_v2_data_20260927
Revises: ingest_v2_20260926
Create Date: 2026-09-27 10:00:00
"""

import json
import uuid
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ingest_v2_data_20260927"
down_revision: Union[str, None] = "ingest_v2_20260926"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot of the stage graph at this revision (migrations don't import app
# code that may change later). Supplementaries get the subset.
STAGES = (
    "source",
    "text_layer",
    "preview",
    "ocr",
    "figures",
    "ocr_repair",
    "metadata",
    "metadata_fallback",
    "outline",
    "highlights",
)
SUPPLEMENTARY_STAGES = {
    "source",
    "text_layer",
    "preview",
    "ocr",
    "figures",
    "ocr_repair",
    "outline",
}
MAX_ATTEMPTS = {"source": 1}  # everything else: 5

MARKDOWN_SOURCES = {
    None: "ocr",
    "": "ocr",
    "mistral": "ocr",
    "openai_ocr_repair": "ocr_repair",
    "pymupdf_fallback": "text_layer",
}
# Page keys that are not part of the Mistral page object.
LEGACY_PAGE_KEYS = {
    "markdown",
    "pymupdf_text",
    "mistral_markdown",
    "openai_ocr",
    "markdown_source",
    "ocr_quality",
}

paper_pages = sa.table(
    "paper_pages",
    sa.column("paper_id", postgresql.UUID(as_uuid=True)),
    sa.column("page_no", sa.Integer()),
    sa.column("width_pt", sa.Float()),
    sa.column("height_pt", sa.Float()),
    sa.column("text_layer", sa.Text()),
    sa.column("ocr_markdown", sa.Text()),
    sa.column("ocr_payload", postgresql.JSONB()),
    sa.column("ocr_quality", postgresql.JSONB()),
    sa.column("repair_markdown", sa.Text()),
    sa.column("markdown", sa.Text()),
    sa.column("markdown_source", sa.String()),
)
paper_figures = sa.table(
    "paper_figures",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("paper_id", postgresql.UUID(as_uuid=True)),
    sa.column("page_no", sa.Integer()),
    sa.column("ocr_image_id", sa.Text()),
    sa.column("label", sa.Text()),
    sa.column("caption", sa.Text()),
    sa.column("bbox", postgresql.JSONB()),
    sa.column("s3_key", sa.Text()),
    sa.column("width", sa.Integer()),
    sa.column("height", sa.Integer()),
)
ingest_stages = sa.table(
    "ingest_stages",
    sa.column("paper_id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.Text()),
    sa.column("status", sa.String()),
    sa.column("attempt", sa.Integer()),
    sa.column("max_attempts", sa.Integer()),
    sa.column("next_attempt_at", sa.DateTime(timezone=True)),
    sa.column("progress_done", sa.Integer()),
    sa.column("progress_total", sa.Integer()),
    sa.column("model_used", sa.Text()),
    sa.column("error_message", sa.Text()),
    sa.column("started_at", sa.DateTime(timezone=True)),
    sa.column("finished_at", sa.DateTime(timezone=True)),
)


# -- 1. papers without OCR ------------------------------------------------------


def delete_papers_without_ocr(conn: sa.Connection) -> None:
    ids = [
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT p.id FROM papers p"
                " WHERE jsonb_typeof(p.ocr) IS DISTINCT FROM 'object'"
                " AND NOT EXISTS (SELECT 1 FROM paper_pages pp WHERE pp.paper_id = p.id)"
                " AND NOT EXISTS (SELECT 1 FROM ingest_stages s WHERE s.paper_id = p.id)"
            )
        )
    ]
    if not ids:
        return

    def run(sql: str) -> sa.CursorResult:
        ids_param = sa.bindparam("ids", type_=postgresql.ARRAY(postgresql.UUID()))
        return conn.execute(sa.text(sql).bindparams(ids_param), {"ids": ids})

    conversations = run(
        "SELECT count(*) FROM conversations WHERE conversable_id = ANY(:ids)"
    ).scalar_one()
    projects = run(
        "SELECT count(*) FROM project_paper WHERE paper_id = ANY(:ids)"
    ).scalar_one()
    if conversations or projects:
        raise RuntimeError(
            f"Papers without OCR {ids} have conversations or project links;"
            " re-OCR or delete them by hand before migrating."
        )
    # Highlights, annotations, tags, repos and supplementaries go by FK
    # cascade. Their S3 objects (PDF, preview) are left behind.
    for paper_id in ids:
        print(f"ingest_v2_data: deleting paper {paper_id} (no Mistral OCR)")
    run("DELETE FROM papers WHERE id = ANY(:ids)")


# -- 2. pages and figures -------------------------------------------------------


def _pt(px: Any, dpi: float) -> Optional[float]:
    if not isinstance(px, (int, float)):
        return None
    return round(float(px) * 72.0 / dpi, 2)


def _dpi(page: dict[str, Any]) -> Optional[float]:
    dims = page.get("dimensions")
    dpi = dims.get("dpi") if isinstance(dims, dict) else None
    return float(dpi) if isinstance(dpi, (int, float)) and dpi > 0 else None


def page_row(paper_id: uuid.UUID, page_no: int, page: dict[str, Any]) -> dict:
    source = MARKDOWN_SOURCES.get(page.get("markdown_source"))
    if source is None:
        raise RuntimeError(
            f"paper {paper_id} page {page_no}: unknown markdown_source"
            f" {page.get('markdown_source')!r}"
        )
    final = page.get("markdown") or ""
    repaired = source == "ocr_repair"
    ocr_markdown = page.get("mistral_markdown") if "mistral_markdown" in page else final

    quality = page.get("ocr_quality")
    quality = dict(quality) if isinstance(quality, dict) else None
    repair = page.get("openai_ocr")
    if isinstance(repair, dict):
        quality = quality or {}
        quality["repair"] = repair
        if repaired:
            quality["model"] = repair.get("model")

    dims = page.get("dimensions") if isinstance(page.get("dimensions"), dict) else {}
    dpi = _dpi(page)
    return {
        "paper_id": paper_id,
        "page_no": page_no,
        "width_pt": _pt(dims.get("width"), dpi) if dpi else None,
        "height_pt": _pt(dims.get("height"), dpi) if dpi else None,
        "text_layer": page.get("pymupdf_text"),
        "ocr_markdown": ocr_markdown,
        "ocr_payload": {k: v for k, v in page.items() if k not in LEGACY_PAGE_KEYS},
        "ocr_quality": quality,
        "repair_markdown": final if repaired else None,
        "markdown": final,
        "markdown_source": source,
    }


def figure_row(
    paper_id: uuid.UUID, figure: dict[str, Any], pages: list[dict[str, Any]]
) -> dict:
    page_no = int(figure["page"])
    page = pages[page_no - 1]
    dpi = _dpi(page)
    box = figure.get("bbox") or {}
    corners = [
        _pt(box.get(k), dpi) if dpi else None
        for k in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")
    ]
    if any(c is None for c in corners):
        raise RuntimeError(
            f"paper {paper_id} figure {figure.get('id')}: no usable bbox/dpi"
        )
    x0, y0, x1, y1 = corners
    image = next(
        (
            img
            for img in page.get("images") or []
            if isinstance(img, dict) and img.get("id") == figure.get("id")
        ),
        {},
    )
    return {
        "id": uuid.uuid4(),
        "paper_id": paper_id,
        "page_no": page_no,
        "ocr_image_id": str(figure["id"]),
        "label": figure.get("label"),
        "caption": figure.get("caption"),
        "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        "s3_key": figure.get("s3_key"),
        "width": image.get("width_px"),
        "height": image.get("height_px"),
    }


def copy_pages_and_figures(conn: sa.Connection) -> None:
    paper_ids = [
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT id FROM papers WHERE jsonb_typeof(ocr) = 'object'"
                " AND NOT EXISTS (SELECT 1 FROM paper_pages pp WHERE pp.paper_id = papers.id)"
                " ORDER BY created_at"
            )
        )
    ]
    for paper_id in paper_ids:
        ocr = conn.execute(
            sa.text("SELECT ocr FROM papers WHERE id = :id"), {"id": paper_id}
        ).scalar_one()
        if isinstance(ocr, str):
            ocr = json.loads(ocr)
        pages = [p for p in ocr.get("pages") or [] if isinstance(p, dict)]
        for i, page in enumerate(pages):
            if page.get("index", i) != i:
                raise RuntimeError(
                    f"paper {paper_id}: page {i} has index {page.get('index')}"
                )
        if pages:
            conn.execute(
                paper_pages.insert(),
                [page_row(paper_id, i + 1, page) for i, page in enumerate(pages)],
            )
        figures = [f for f in ocr.get("figures") or [] if isinstance(f, dict)]
        if figures:
            conn.execute(
                paper_figures.insert(),
                [figure_row(paper_id, f, pages) for f in figures],
            )


# -- 3. stage rows --------------------------------------------------------------


def create_stage_rows(conn: sa.Connection) -> None:
    papers = conn.execute(
        sa.text(
            "SELECT p.id, p.supplementary_of_paper_id IS NOT NULL,"
            " p.preview_url IS NOT NULL, p.generated_outline IS NOT NULL,"
            " p.ocr->>'model',"
            " (SELECT count(*) FROM paper_pages pp WHERE pp.paper_id = p.id),"
            " (SELECT count(*) FROM paper_figures pf WHERE pf.paper_id = p.id)"
            " FROM papers p"
            " WHERE EXISTS (SELECT 1 FROM paper_pages pp WHERE pp.paper_id = p.id)"
            " AND NOT EXISTS (SELECT 1 FROM ingest_stages s WHERE s.paper_id = p.id)"
        )
    ).all()
    now = conn.execute(sa.text("SELECT now()")).scalar_one()
    rows: list[dict[str, Any]] = []
    for (
        paper_id,
        is_supp,
        has_preview,
        has_outline,
        ocr_model,
        n_pages,
        n_figs,
    ) in papers:
        for name in STAGES:
            if is_supp and name not in SUPPLEMENTARY_STAGES:
                continue
            done = {
                "preview": has_preview,
                "outline": has_outline,
            }.get(name, True)
            row: dict[str, Any] = {
                "paper_id": paper_id,
                "name": name,
                "status": "succeeded" if done else "queued",
                "attempt": 1 if done else 0,
                "max_attempts": MAX_ATTEMPTS.get(name, 5),
                "next_attempt_at": None if done else now,
                "progress_done": None,
                "progress_total": None,
                "model_used": None,
                "error_message": None,
                "started_at": now if done else None,
                "finished_at": now if done else None,
            }
            if name == "metadata_fallback":
                row.update(
                    status="skipped",
                    attempt=0,
                    started_at=None,
                    error_message="Migrated: metadata came from the old pipeline.",
                )
            elif name == "ocr":
                row.update(
                    progress_done=n_pages, progress_total=n_pages, model_used=ocr_model
                )
            elif name == "figures":
                row.update(progress_done=n_figs, progress_total=n_figs)
            rows.append(row)
    for start in range(0, len(rows), 500):
        conn.execute(ingest_stages.insert(), rows[start : start + 500])


# -- 5. search vector -----------------------------------------------------------

SEARCH_TRIGGERS_UP = r"""
CREATE FUNCTION paper_search_vector(pid uuid, title text) RETURNS tsvector
LANGUAGE sql STABLE AS $$
    SELECT setweight(to_tsvector('pg_catalog.english', coalesce(title, '')), 'A') ||
           setweight(to_tsvector('pg_catalog.english', coalesce(
               (SELECT string_agg(markdown, E'\n\n' ORDER BY page_no)
                  FROM paper_pages WHERE paper_id = pid), '')), 'D')
$$;

DROP TRIGGER tsvectorupdate ON papers;
DROP FUNCTION paper_content_trigger();

CREATE FUNCTION papers_search_title_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.ts_vector := paper_search_vector(NEW.id, NEW.title);
    RETURN NEW;
END
$$;
CREATE TRIGGER papers_search_title BEFORE INSERT OR UPDATE OF title ON papers
    FOR EACH ROW EXECUTE FUNCTION papers_search_title_trigger();

-- Deferred to commit, and run once per paper each time the deferred events
-- fire (at COMMIT, or at SET CONSTRAINTS ... IMMEDIATE): the first row event
-- recomputes from the final pages, the rest see the transaction-local
-- marker (paper id @ the firing statement's start) and skip. So a stage
-- that updates every page one statement at a time costs one recompute.
CREATE FUNCTION paper_pages_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    pid uuid;
    marker text;
    done text := coalesce(current_setting('openpaper.search_refreshed', true), '');
BEGIN
    IF TG_OP = 'DELETE' THEN
        pid := OLD.paper_id;
    ELSE
        IF TG_OP = 'INSERT' AND NEW.markdown IS NULL THEN
            RETURN NULL;
        END IF;
        pid := NEW.paper_id;
    END IF;
    marker := pid::text || '@' || statement_timestamp()::text || ',';
    IF position(marker IN done) > 0 THEN
        RETURN NULL;
    END IF;
    PERFORM set_config('openpaper.search_refreshed', done || marker, true);
    UPDATE papers SET ts_vector = paper_search_vector(id, title) WHERE id = pid;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER paper_pages_search
    AFTER INSERT OR UPDATE OF markdown OR DELETE ON paper_pages
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION paper_pages_search_trigger();

UPDATE papers SET ts_vector = paper_search_vector(id, title);
"""

SEARCH_TRIGGERS_DOWN = r"""
DROP TRIGGER paper_pages_search ON paper_pages;
DROP FUNCTION paper_pages_search_trigger();
DROP TRIGGER papers_search_title ON papers;
DROP FUNCTION papers_search_title_trigger();
DROP FUNCTION paper_search_vector(uuid, text);

CREATE FUNCTION paper_content_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.ts_vector :=
        setweight(to_tsvector('pg_catalog.english', coalesce(NEW.title,'')), 'A') ||
        setweight(to_tsvector('pg_catalog.english', coalesce(NEW.raw_content,'')), 'D');
    RETURN NEW;
END
$$;
CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE ON papers
    FOR EACH ROW EXECUTE FUNCTION paper_content_trigger();

UPDATE papers SET title = title;
"""


def upgrade() -> None:
    conn = op.get_bind()
    delete_papers_without_ocr(conn)
    copy_pages_and_figures(conn)
    create_stage_rows(conn)
    op.execute("UPDATE highlights SET origin = 'ai' WHERE role = 'assistant'")
    # `Annotation.origin` is on the model (d82caa5) but ingest_v2_20260926
    # only added `highlights.origin`; without the column every annotation
    # query fails. IF NOT EXISTS in case the schema migration gains it.
    op.execute(
        "ALTER TABLE annotations"
        " ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'user'"
    )
    op.execute("UPDATE annotations SET origin = 'ai' WHERE role = 'assistant'")
    op.execute(SEARCH_TRIGGERS_UP)


def downgrade() -> None:
    # The deleted pymupdf-only paper is not restored; `annotations.origin`
    # stays (the model has it).
    op.execute(SEARCH_TRIGGERS_DOWN)
    op.execute("UPDATE highlights SET origin = 'user'")
    op.execute("UPDATE annotations SET origin = 'user'")
    op.execute("DELETE FROM ingest_stages")
    op.execute("DELETE FROM paper_figures")
    op.execute("DELETE FROM paper_pages")
