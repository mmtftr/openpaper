"""The chat conversations of one paper (the conversation API does the rest)."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.crud.conversation_crud import conversation_crud
from app.database.crud.paper_crud import paper_crud
from app.database.database import get_db
from app.schemas.paper import PaperConversationSummary
from app.schemas.user import CurrentUser

router = APIRouter()


@router.get("/conversations")
def get_paper_conversations(
    paper_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> List[PaperConversationSummary]:
    """List every conversation tied to this paper (newest-updated first)."""
    document = paper_crud.get(db, id=paper_id, user=current_user)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    conversations = conversation_crud.get_document_conversations(
        db, paper_id=paper_id, current_user=current_user
    )
    conversations = sorted(conversations, key=lambda c: c.updated_at, reverse=True)

    return [
        PaperConversationSummary(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in conversations
    ]
