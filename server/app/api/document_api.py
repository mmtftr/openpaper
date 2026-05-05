"""Document endpoints — slice 1.

Surfaces the per-paper MAIN doc that the Milkdown editor edits. Conflict
semantics live here (413 over-cap, 409 on stale revision) so the agent's
`write_main_doc` tool in slice 2 can reuse the same CRUD helpers and inherit
identical behavior.
"""

import logging
from typing import Optional
from uuid import UUID

from app.auth.dependencies import get_required_user
from app.database.crud.document_crud import (
    RevisionMismatch,
    document_crud,
)
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

document_router = APIRouter()

# 1 MB hard cap on document content. Surfaced to the agent in slice 2's
# write_main_doc description so the model doesn't try multi-MB writes.
MAX_DOCUMENT_CONTENT_BYTES = 1_000_000


class DocumentResponse(BaseModel):
    id: str
    paper_id: Optional[str]
    title: str
    content: str
    revision: int
    kind: str
    updated_at: Optional[str] = None


class UpdateDocumentRequest(BaseModel):
    content: str
    expected_revision: int


def _serialize(doc) -> DocumentResponse:
    return DocumentResponse(
        id=str(doc.id),
        paper_id=str(doc.paper_id) if doc.paper_id else None,
        title=str(doc.title or ""),
        content=str(doc.content or ""),
        revision=int(doc.revision),
        kind=str(doc.kind),
        updated_at=doc.updated_at.isoformat() if doc.updated_at else None,
    )


@document_router.get("/main")
async def get_main_document(
    paper_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> DocumentResponse:
    """Return the MAIN document for a paper, creating it on first call.

    Idempotent: subsequent calls return the same row. The editor uses this on
    mount and to reload after a 409 conflict.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    try:
        paper_uuid = UUID(paper_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid paper_id")

    paper_title = str(getattr(paper, "title", "") or "").strip()
    default_content = f"# {paper_title}\n\n" if paper_title else ""
    default_title = paper_title or "Untitled"

    doc = document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_uuid,
        user=current_user,
        default_content=default_content,
        default_title=default_title,
    )
    return _serialize(doc)


@document_router.put("/{document_id}")
async def update_document(
    document_id: str,
    body: UpdateDocumentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
):
    """Replace document content, gated by `expected_revision`.

    413 if content exceeds the hard size cap.
    409 (with `current_revision` and `current_content`) on stale revision so
        the editor can prompt the user to reload without an extra round-trip.
    """
    if len(body.content.encode("utf-8")) > MAX_DOCUMENT_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Document exceeds {MAX_DOCUMENT_CONTENT_BYTES} byte cap"
            ),
        )

    doc = document_crud.get(db, id=document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        updated = document_crud.update_with_revision_check(
            db,
            doc=doc,
            content=body.content,
            expected_revision=body.expected_revision,
            user=current_user,
        )
    except RevisionMismatch as e:
        return JSONResponse(
            status_code=409,
            content={
                "error": "revision_mismatch",
                "current_revision": e.current_revision,
                "current_content": e.current_content,
            },
        )
    except PermissionError:
        raise HTTPException(status_code=404, detail="Document not found")

    return _serialize(updated)
