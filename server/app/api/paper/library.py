"""The owner's paper lists: library, reading list, relevant papers."""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.database.models import Paper, PaperStatus
from app.database.queries import library
from app.helpers.s3 import s3_service
from app.schemas.paper import (
    ActivePaper,
    ActivePapersResponse,
    LibraryPaper,
    LibraryPapersResponse,
    PaperTagResponse,
    RelevantPaper,
    RelevantPapersResponse,
)
from app.schemas.user import CurrentUser

router = APIRouter()


def _list_item_fields(paper: Paper) -> Dict[str, Any]:
    """Fields shared by every paper-list item (see `RelevantPaper`)."""
    return {
        "id": paper.id,
        "title": paper.title,
        "created_at": paper.created_at,
        "abstract": paper.abstract,
        "authors": paper.authors,
        "institutions": paper.institutions,
        "keywords": paper.keywords,
        "status": paper.status,
        "preview_url": paper.preview_url,
        "size_in_kb": paper.size_in_kb,
    }


@router.get("/all")
def get_paper_ids(
    db: Session = Depends(get_db),
    detailed: bool = False,
    archived: bool = False,
    current_user: CurrentUser = Depends(get_required_user),
) -> LibraryPapersResponse:
    """
    The library: the owner's unarchived papers, or with `archived=true` the
    archived ones.
    """
    papers = library.library_papers(db, user=current_user, archived=archived)

    # Bulk retrieve presigned URLs for all papers (optimized with parallelization)
    file_urls = {}
    if detailed:
        file_urls = s3_service.get_cached_presigned_urls_bulk(
            db=db,
            papers=papers,
        )

    processing = library.processing_ids(db, [p.id for p in papers])
    return LibraryPapersResponse(
        papers=[
            LibraryPaper(
                **_list_item_fields(paper),
                publish_date=paper.publish_date,
                file_url=file_urls.get(str(paper.id)),
                tags=[PaperTagResponse.model_validate(tag) for tag in paper.tags],
                processing=paper.id in processing,
                archived_at=paper.archived_at,
            )
            for paper in papers
        ]
    )


@router.get("/active")
def get_active_paper_ids(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ActivePapersResponse:
    """
    The unarchived papers being read (the command menu).
    """
    papers: List[Paper] = library.library_papers(
        db, user=current_user, status=PaperStatus.reading
    )
    return ActivePapersResponse(
        papers=[
            ActivePaper(**_list_item_fields(paper), publish_date=paper.publish_date)
            for paper in papers
        ]
    )


@router.get("/relevant")
def get_relevant_papers(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> RelevantPapersResponse:
    """
    Get the most relevant papers uploaded by the user
    """
    papers = library.relevant_papers(db, user=current_user)
    return RelevantPapersResponse(
        papers=[RelevantPaper(**_list_item_fields(paper)) for paper in papers]
    )
