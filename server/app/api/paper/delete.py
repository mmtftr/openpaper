"""Deleting a paper: its rows, its S3 objects and its repo snapshot."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.crud.projects.project_paper_crud import project_paper_crud
from app.database.database import get_db
from app.database.models import Paper
from app.helpers.s3 import s3_service
from app.ingest import content, storage
from app.ingest.models import IngestStage
from app.schemas.paper import MessageResponse
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter()


def _paper_files(db: Session, paper: Paper) -> tuple[list[str], set[str]]:
    """A paper's S3 objects as (prefixes, keys): everything under
    `papers/{id}/` plus, for papers the old pipeline stored, the PDF /
    preview / figure images it kept elsewhere (`uploads/…`, `figures/{id}/…`)."""
    prefix = storage.paper_prefix(paper.id)
    keys = [
        str(paper.s3_object_key) if paper.s3_object_key else None,
        storage.key_from_public_url(s3_service, paper.preview_url),
        *(f.s3_key for f in content.figures(db, paper.id)),
    ]
    return (
        [prefix, f"figures/{paper.id}/"],
        {k for k in keys if k and not k.startswith(prefix)},
    )


@router.delete("")
def delete_pdf(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> MessageResponse:
    """
    Delete a document by ID
    """
    paper = paper_crud.get(db, id=id, user=current_user)

    if not paper:
        raise HTTPException(status_code=404, detail="Document not found")

    projects = project_paper_crud.get_projects_by_paper_id(
        db, paper_id=id, user=current_user
    )
    if len(projects) > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete document associated with projects. Please remove the document from all projects before deleting.",
        )

    # Collect the files while the rows exist (figure keys); supplementary
    # materials go with the paper (FK CASCADE), so their files go too.
    files = [
        _paper_files(db, doc)
        for doc in [
            paper,
            *paper_crud.list_supplementary_for(
                db, parent_paper_id=id, user=current_user
            ),
        ]
    ]

    # Lock the stage rows before the paper row, in the ingest worker's order
    # (it locks a paper's stages, then its `save()` may update the paper);
    # the cascade would otherwise take them the other way round.
    db.execute(
        select(IngestStage.paper_id)
        .join(Paper, Paper.id == IngestStage.paper_id)
        .where((Paper.id == id) | (Paper.supplementary_of_paper_id == id))
        .order_by(IngestStage.paper_id, IngestStage.name)
        .with_for_update(of=IngestStage)
    ).all()
    paper_crud.remove(db, id=id, user=current_user)

    try:
        for prefixes, keys in files:
            for prefix in prefixes:
                storage.delete_prefix(s3_service, prefix)
            for key in keys:
                storage.delete_key(s3_service, key)

        # The paper_repos row goes with the paper (FK CASCADE), but the
        # ingested snapshot lives on a volume — drop it here.
        from app.llm.repo import storage as repo_storage

        repo_storage.delete_paper_snapshots(str(id))
    except Exception as e:
        logger.error(f"Error deleting document files: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting document: {str(e)}"
        )

    return MessageResponse(message="Document deleted")
