"""Document endpoints.

Slice 1 surfaced the per-paper MAIN doc the Milkdown editor edits. Conflict
semantics live here (413 over-cap, 409 on stale revision) so the agent's
`write_doc` tool from slice 2 reuses the same CRUD helpers and inherits
identical behavior.

Slice 3 adds list/create/rename/delete for additional NOTE docs scoped to a
paper. MAIN docs remain non-deletable and non-renameable (their title `main`
is the agent's address for them); content-edit goes through the existing
optimistic-locking path on PUT.
"""

import logging
from typing import List, Literal, Optional
from uuid import UUID

from app.auth.dependencies import get_required_user
from app.database.crud.document_crud import (
    RevisionMismatch,
    document_crud,
)
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.api.errors import ApiError
from app.database.models import DocumentKind
from app.schemas.json_datetime import IsoDatetime
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

document_router = APIRouter()

# 1 MB hard cap on document content. Surfaced to the agent in the write_doc
# tool description so the model doesn't try multi-MB writes.
MAX_DOCUMENT_CONTENT_BYTES = 1_000_000


class DocumentResponse(BaseModel):
    id: UUID
    paper_id: Optional[UUID]
    title: str
    content: str
    revision: int
    kind: DocumentKind
    updated_at: Optional[IsoDatetime] = None


class DocumentSummary(BaseModel):
    """List-view shape — drops `content` so the doc-switcher doesn't pull
    every doc's body just to render a dropdown."""

    id: UUID
    paper_id: Optional[UUID]
    title: str
    revision: int
    kind: DocumentKind
    updated_at: Optional[IsoDatetime] = None


class RevisionConflictError(ApiError):
    """409 body for a stale `expected_revision`: carries the server's current
    state so the editor can offer a reload without another round-trip."""

    error: Literal["revision_mismatch"] = "revision_mismatch"
    current_revision: int
    current_content: str


class UpdateDocumentRequest(BaseModel):
    content: str
    expected_revision: int


class CreateDocumentRequest(BaseModel):
    paper_id: UUID
    title: Optional[str] = None


class RenameDocumentRequest(BaseModel):
    title: str


# Hard cap on doc-title length; same value used by the client picker's input.
MAX_TITLE_LENGTH = 200

# Reserved title for the MAIN doc. The agent addresses MAIN as `main` via
# read_doc/write_doc — letting a NOTE share that name would silently shadow
# the agent's view of the user's primary writeup.
RESERVED_MAIN_NAME = "main"


def _serialize(doc) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        paper_id=doc.paper_id,
        title=str(doc.title or ""),
        content=str(doc.content or ""),
        revision=int(doc.revision),
        kind=doc.kind,
        updated_at=doc.updated_at,
    )


def _serialize_summary(doc) -> DocumentSummary:
    return DocumentSummary(
        id=doc.id,
        paper_id=doc.paper_id,
        title=str(doc.title or ""),
        revision=int(doc.revision),
        kind=doc.kind,
        updated_at=doc.updated_at,
    )


@document_router.get("")
def list_documents(
    paper_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> List[DocumentSummary]:
    """List all docs (MAIN + NOTE) for a paper owned by the current user.

    Returns summaries — no content — so the doc-switcher renders cheaply.
    Auto-creates MAIN if missing so the list is never empty for a paper the
    user can access; matches the GET /main contract.
    """
    paper = paper_crud.get(db, id=paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    paper_title = str(getattr(paper, "title", "") or "").strip()
    default_content = f"# {paper_title}\n\n" if paper_title else ""
    document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_id,
        user=current_user,
        default_content=default_content,
        default_title=RESERVED_MAIN_NAME,
    )

    docs = document_crud.list_for_paper(db, paper_id=paper_id, user=current_user)
    return [_serialize_summary(d) for d in docs]


@document_router.post("")
def create_document(
    body: CreateDocumentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> DocumentResponse:
    """Create a NOTE doc attached to a paper. Body content always starts empty;
    the user types into the editor right after switching to it."""
    paper = paper_crud.get(db, id=body.paper_id, user=current_user)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    title = (body.title or "").strip() or "Untitled"
    if len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Title exceeds {MAX_TITLE_LENGTH} characters",
        )
    if title.lower() == RESERVED_MAIN_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"'{RESERVED_MAIN_NAME}' is reserved for the main doc",
        )

    doc = document_crud.create_note_for_paper(
        db, paper_id=body.paper_id, user=current_user, title=title
    )
    return _serialize(doc)


@document_router.get("/main")
def get_main_document(
    paper_id: UUID,
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

    paper_title = str(getattr(paper, "title", "") or "").strip()
    default_content = f"# {paper_title}\n\n" if paper_title else ""

    doc = document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_id,
        user=current_user,
        default_content=default_content,
        default_title=RESERVED_MAIN_NAME,
    )
    return _serialize(doc)


@document_router.put(
    "/{document_id}",
    responses={409: {"model": RevisionConflictError, "description": "Stale revision"}},
)
def update_document(
    document_id: UUID,
    body: UpdateDocumentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> DocumentResponse:
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
        conflict = RevisionConflictError(
            detail="Document was changed since it was loaded",
            current_revision=e.current_revision,
            current_content=e.current_content,
        )
        return JSONResponse(status_code=409, content=conflict.model_dump())  # type: ignore[return-value]
    except PermissionError:
        raise HTTPException(status_code=404, detail="Document not found")

    return _serialize(updated)


@document_router.get("/{document_id}")
def get_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> DocumentResponse:
    """Return a single doc by id (any kind). Used by the editor when it
    switches to a NOTE doc — content is what the Milkdown editor hydrates from."""
    doc = document_crud.get(db, id=document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize(doc)


@document_router.patch("/{document_id}")
def rename_document(
    document_id: UUID,
    body: RenameDocumentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> DocumentResponse:
    """Rename a doc. Title-only here — content updates go through PUT so they
    keep the optimistic-locking semantics that the agent relies on."""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Title exceeds {MAX_TITLE_LENGTH} characters",
        )
    doc = document_crud.get(db, id=document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if str(doc.kind) == DocumentKind.MAIN.value:
        # The MAIN doc's title is the agent's address for it; clients can't
        # rename it without breaking read_doc('main') / write_doc('main').
        raise HTTPException(status_code=400, detail="Cannot rename the main doc")
    if title.lower() == RESERVED_MAIN_NAME:
        raise HTTPException(
            status_code=400,
            detail=f"'{RESERVED_MAIN_NAME}' is reserved for the main doc",
        )
    try:
        updated = document_crud.update_title(
            db, doc=doc, title=title, user=current_user
        )
    except PermissionError:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize(updated)


@document_router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> None:
    """Delete a NOTE doc. MAIN docs are protected — returns 400 to keep the
    paper's primary writeup from disappearing accidentally."""
    doc = document_crud.get(db, id=document_id, user=current_user)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if str(doc.kind) == DocumentKind.MAIN.value:
        raise HTTPException(status_code=400, detail="Cannot delete the main doc")
    try:
        document_crud.delete_doc(db, doc=doc, user=current_user)
    except PermissionError:
        raise HTTPException(status_code=404, detail="Document not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return None
