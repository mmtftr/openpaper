"""One paper: its record, the owner's edits, its outline and its markdown."""

import re
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.database import get_db
from app.database.models import Paper, PaperStatus
from app.database.telemetry import track_event
from app.helpers.s3 import s3_service
from app.ingest import content, footnotes, paragraphs
from app.ingest.models import METADATA_FIELDS, MetadataSource
from app.llm.paper_outline import OutlineEntry
from app.schemas.paper import (
    PaperDetail,
    PaperMarkdown,
    PaperRecord,
    UpdatePaperFieldsRequest,
)
from app.schemas.user import CurrentUser

router = APIRouter()


# `![alt](src)` / `![alt](src "title")` image references in page markdown.
_MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")


def _paper_markdown_payload(db: Session, paper: Paper) -> PaperMarkdown:
    """The whole paper as one markdown document (the markdown reader).

    Mistral numbers images per OCR batch, so "img-0.jpeg" can occur on
    several pages; each page's image references are pointed at the figure's
    row id instead, which the figure endpoint resolves unambiguously.
    """
    figure_ids = {
        (fig.page_no, fig.ocr_image_id): str(fig.id)
        for fig in content.figures(db, paper.id)
    }

    def page_text(page: content.Page) -> str:
        def point_at_row(m: re.Match[str]) -> str:
            row_id = figure_ids.get((page.page_no, m.group(2)))
            return m.group(1) + row_id + m.group(3) if row_id else m.group(0)

        return _MARKDOWN_IMAGE_RE.sub(point_at_row, page.markdown).strip()

    pages = content.pages(db, paper.id)
    markdown = "\n\n".join(page_text(page) for page in pages).strip()
    # Each page carries its own footnote definitions; the reader wants them
    # all at the end. Then the paragraphs that page breaks (and the figures,
    # page numbers and running headers in them) cut mid-sentence are joined.
    markdown = paragraphs.rejoin(footnotes.collect_definitions(markdown))
    return PaperMarkdown(markdown=markdown, source="mistral")


@router.post("/status")
def set_paper_status(
    paper_id: uuid.UUID,
    status: PaperStatus,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperRecord:
    """
    Set the status of a paper
    """
    target_paper = paper_crud.get(db, id=paper_id, user=current_user)

    if not target_paper:
        raise HTTPException(status_code=404, detail=f"No document with id {paper_id}")

    paper_update = PaperUpdate(status=status)
    updated_paper = paper_crud.update(
        db=db, db_obj=target_paper, obj_in=paper_update, user=current_user
    )

    track_event(
        "paper_status_updated",
        properties={
            "paper_id": str(updated_paper.id),
            "status": updated_paper.status,
        },
        user_id=str(current_user.id),
    )

    return PaperRecord.model_validate(updated_paper)


@router.patch("")
def update_paper_fields(
    paper_id: uuid.UUID,
    request: UpdatePaperFieldsRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperRecord:
    """
    Update editable fields of a paper (title, authors, abstract, etc.)
    """
    target_paper = paper_crud.get(db, id=paper_id, user=current_user)

    if not target_paper:
        raise HTTPException(status_code=404, detail=f"No document with id {paper_id}")

    update_data = request.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Owner edits win over later metadata lookups (reprocess, fallback).
    # A new dict, so SQLAlchemy sees the JSONB change.
    sources: dict[str, str] = dict(target_paper.metadata_source or {})
    for name in update_data:
        if name in METADATA_FIELDS:
            sources[name] = MetadataSource.USER.value

    updated_paper = paper_crud.update(
        db=db,
        db_obj=target_paper,
        obj_in={**update_data, "metadata_source": sources},
        user=current_user,
    )

    track_event(
        "paper_fields_updated",
        properties={
            "paper_id": str(updated_paper.id),
            "updated_fields": list(update_data.keys()),
        },
        user_id=str(current_user.id),
    )

    return PaperRecord.model_validate(updated_paper)


@router.get("")
def get_pdf(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperDetail:
    """
    Get a document by ID
    """
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)

    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    signed_url = s3_service.get_cached_presigned_url(
        db,
        paper_id=str(paper.id),
        object_key=str(paper.s3_object_key),
        current_user=current_user,
    )
    if not signed_url:
        raise HTTPException(status_code=404, detail="File not found")

    return PaperDetail.model_validate(paper).model_copy(update={"file_url": signed_url})


@router.get("/outline", response_model=List[OutlineEntry])
def get_paper_outline(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """The outline the ingest `outline` stage built (empty until it ran)."""
    paper = paper_crud.get(db, id=id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")
    return paper.generated_outline or []


@router.get("/markdown")
def get_paper_markdown(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> PaperMarkdown:
    paper = paper_crud.get(db, id=id, user=current_user, update_last_accessed=True)
    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    return _paper_markdown_payload(db, paper)
