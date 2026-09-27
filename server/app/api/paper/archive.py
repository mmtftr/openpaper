"""Archiving papers: out of the library lists, still openable by URL."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.schemas.paper import ArchivePapersRequest, ArchivePapersResponse
from app.schemas.user import CurrentUser

router = APIRouter()


@router.post("/archive")
def archive_papers(
    request: ArchivePapersRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ArchivePapersResponse:
    """Archive (or with `archived: false` unarchive) one or many papers.

    Ids that aren't the owner's are skipped; 404 if none are.
    """
    paper_ids, archived_at = paper_crud.set_archived(
        db, paper_ids=request.paper_ids, archived=request.archived, user=current_user
    )
    if not paper_ids:
        raise HTTPException(status_code=404, detail="Document not found")
    return ArchivePapersResponse(paper_ids=paper_ids, archived_at=archived_at)
