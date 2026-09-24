"""Ingest v2 tables (docs/INGEST_DESIGN.md §3).

- `ingest_stages`: one row per stage per paper. It IS the work queue (the
  worker polls due `queued` rows) and the progress record the UI reads.
- `ingest_worker`: a single heartbeat row, so the UI can say "worker offline".
- `paper_pages`: per-page text from every source plus the chosen final text.
- `paper_figures`: figure boxes found by OCR and their rendered images.

Registered on the shared `Base` (imported at the bottom of
`app.database.models`), so alembic and the app see them with everything else.
Every table also gets `created_at` / `updated_at` from `Base`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Optional

# pyright ignores: SQLAlchemy 2.0 names the 1.4-era sqlalchemy2-stubs
# (resolved first by pyright) don't know. Runtime is SQLAlchemy 2.0.
from sqlalchemy import (  # type: ignore
    UUID,  # pyright: ignore[reportAttributeAccessIssue]
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (  # type: ignore
    Mapped,
    mapped_column,  # pyright: ignore[reportAttributeAccessIssue]
)

from app.core.errors import ErrorKind
from app.database.models import Base


class StageStatus(StrEnum):
    PENDING = "pending"  # waiting for its dependencies
    QUEUED = "queued"  # ready; runs when `next_attempt_at` is due
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"  # not needed (counts as done for dependents)
    FAILED = "failed"  # permanent/config error or out of attempts
    BLOCKED = "blocked"  # an upstream stage failed


# A dependency in one of these states lets its dependents run.
DONE_STATUSES = frozenset({StageStatus.SUCCEEDED, StageStatus.SKIPPED})


class MarkdownSource(StrEnum):
    """Which text became `PaperPage.markdown`."""

    OCR = "ocr"  # Mistral OCR as returned
    OCR_REPAIR = "ocr_repair"  # vision re-OCR of a page that scored badly
    TEXT_LAYER = "text_layer"  # pymupdf text (both OCR paths unusable)


class MetadataSource(StrEnum):
    """Values of `Paper.metadata_source[field]`."""

    USER = "user"  # edited by the owner: ingest never overwrites it
    EMBEDDED = "embedded"  # the PDF's own XMP/info dictionary
    CROSSREF = "crossref"
    OPENALEX = "openalex"
    ARXIV = "arxiv"
    LLM = "llm"  # extracted by the model from the text: unverified


# The `Paper` columns `metadata_source` may describe.
METADATA_FIELDS = (
    "title",
    "authors",
    "abstract",
    "publish_date",
    "journal",
    "publisher",
    "doi",
    "arxiv_id",
    "openalex_id",
    "keywords",
    "institutions",
)


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


def _check_in(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{v}'" for v in _enum_values(enum))
    return CheckConstraint(f"{column} IN ({values})", name=name)


def _str_enum(enum: type[StrEnum]) -> SAEnum:
    # VARCHAR + our own CHECK (named, in `__table_args__`) rather than a
    # Postgres ENUM type: adding a value later is a one-line migration.
    return SAEnum(
        enum,
        native_enum=False,
        create_constraint=False,
        length=16,
        values_callable=_enum_values,
    )


class IngestStage(Base):
    __tablename__ = "ingest_stages"
    __table_args__ = (
        _check_in("status", StageStatus, "ck_ingest_stages_status"),
        _check_in("error_kind", ErrorKind, "ck_ingest_stages_error_kind"),
        # The worker's poll: due queued rows, oldest first.
        Index(
            "ix_ingest_stages_due",
            "next_attempt_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[StageStatus] = mapped_column(
        _str_enum(StageStatus),
        nullable=False,
        default=StageStatus.PENDING,
        server_default=StageStatus.PENDING.value,
    )
    # Attempts started so far (the running one included).
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    # When a `queued` row may run (now for fresh work, later for backoff).
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    progress_done: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # e.g. "codex_proxy/gpt-6-astra" or "mistral-ocr-latest".
    model_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Last failure (kept while a retry is pending; cleared on success), or
    # the skip reason for `skipped`.
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_kind: Mapped[Optional[ErrorKind]] = mapped_column(
        _str_enum(ErrorKind), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<IngestStage {self.paper_id}/{self.name} {self.status}>"


# The UI calls the worker offline when `last_seen` is older than this.
WORKER_STALE_AFTER_SECONDS = 15.0


class IngestWorker(Base):
    """The one ingest worker's heartbeat (single row, id = 1)."""

    __tablename__ = "ingest_worker"
    __table_args__ = (CheckConstraint("id = 1", name="ck_ingest_worker_single"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, default=1
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hostname: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<IngestWorker pid={self.pid} last_seen={self.last_seen}>"


class PaperPage(Base):
    """One PDF page. `page_no` is 1-based.

    Stages fill their own columns: `text_layer` (text_layer), `ocr_markdown`
    + `ocr_payload` (ocr), `ocr_quality` + `repair_markdown` + `markdown` +
    `markdown_source` (ocr_repair). `markdown` is what chat, search and the
    outline read.
    """

    __tablename__ = "paper_pages"
    __table_args__ = (
        CheckConstraint("page_no >= 1", name="ck_paper_pages_page_no"),
        _check_in("markdown_source", MarkdownSource, "ck_paper_pages_source"),
    )

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Page size in PDF points (pymupdf `page.rect`), for coordinate
    # conversion; the OCR image size/DPI is in `ocr_payload.dimensions`.
    width_pt: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    height_pt: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    text_layer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The Mistral OCR page object minus `markdown` (and image base64):
    # index, dimensions {dpi, width, height}, images [...], blocks, tables,
    # hyperlinks, header, footer, confidence_scores.
    ocr_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Scoring of OCR vs the text layer: {status, reason, ocr_token_count,
    # pymupdf_token_count, common_token_count, ocr_token_precision,
    # pymupdf_token_recall} (+ "model" when the page was repaired).
    ocr_quality: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    repair_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown_source: Mapped[Optional[MarkdownSource]] = mapped_column(
        _str_enum(MarkdownSource), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PaperPage {self.paper_id} p{self.page_no}>"


class PaperFigure(Base):
    """A figure region found by OCR, rendered to S3 by the `figures` stage.

    `bbox` is in PDF points with a TOP-LEFT origin (pymupdf's convention):
    {"x0", "y0", "x1", "y1"}. Mistral's boxes are in OCR-image pixels; convert
    with the page's OCR DPI (`ocr_payload.dimensions.dpi`): pt = px * 72 / dpi.

    Image keys are never reused or deleted while the paper exists — saved
    chat history refers to them.
    """

    __tablename__ = "paper_figures"
    __table_args__ = (
        # Mistral numbers images per request ("img-0.jpeg", ...), and OCR
        # runs in batches, so the id is only unique within a page.
        UniqueConstraint(
            "paper_id", "page_no", "ocr_image_id", name="uq_paper_figures_image"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    ocr_image_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # "Figure 3"
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bbox: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Rendered image size in pixels.
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<PaperFigure {self.paper_id} p{self.page_no} {self.ocr_image_id}>"


__all__ = [
    "DONE_STATUSES",
    "METADATA_FIELDS",
    "WORKER_STALE_AFTER_SECONDS",
    "IngestStage",
    "IngestWorker",
    "MarkdownSource",
    "MetadataSource",
    "PaperFigure",
    "PaperPage",
    "StageStatus",
]
