"""A paper's supplementary materials (uploaded through the upload router)."""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.schemas.paper import SupplementaryMaterialItem
from app.schemas.user import CurrentUser

router = APIRouter()


@router.get("/{paper_id}/supplementary")
def list_supplementary_materials(
    paper_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_required_user),
    db: Session = Depends(get_db),
) -> List[SupplementaryMaterialItem]:
    """Supplementary materials attached to a paper, oldest first."""
    parent = paper_crud.get(db, id=paper_id, user=current_user)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent paper not found")

    supplementary_papers = paper_crud.list_supplementary_for(
        db, parent_paper_id=paper_id, user=current_user
    )
    return [
        SupplementaryMaterialItem(
            id=paper.id,
            # Supplementaries get no metadata lookup: the PDF's embedded
            # title (text_layer) or else the uploaded file's name.
            title=paper.title or paper.source_filename,
            preview_url=paper.preview_url,
            page_count=paper.page_count,
            created_at=paper.created_at,
        )
        for paper in sorted(
            supplementary_papers,
            key=lambda p: p.created_at or datetime.min.replace(tzinfo=timezone.utc),
        )
    ]
