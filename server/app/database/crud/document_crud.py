"""CRUD for the `documents` table.

Slice 1 only deals with the per-paper MAIN doc; the read/write helpers
encapsulate the optimistic-locking semantics so the route handler and the
agent's `write_main_doc` tool (slice 2) share the same atomic check.
"""

import logging
from typing import Optional
from uuid import UUID

from app.database.crud.base_crud import CRUDBase
from app.database.crud.sanitization import sanitize_for_postgres
from app.database.models import Document, DocumentKind
from app.schemas.user import CurrentUser
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class RevisionMismatch(Exception):
    """Raised by `update_with_revision_check` when expected_revision is stale.

    Carries the current state so the caller can return it to the client (or
    surface it back to the agent for a merge attempt) without another query.
    """

    def __init__(self, current_revision: int, current_content: str):
        self.current_revision = current_revision
        self.current_content = current_content
        super().__init__(f"revision_mismatch (current={current_revision})")


class DocumentCreate(BaseModel):
    user_id: UUID
    paper_id: Optional[UUID] = None
    parent_document_id: Optional[UUID] = None
    kind: str = DocumentKind.NOTE.value
    title: str = "Untitled"
    content: str = ""


class DocumentUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None


class DocumentCRUD(CRUDBase[Document, DocumentCreate, DocumentUpdate]):
    def get_main_for_paper(
        self, db: Session, *, paper_id: UUID | str, user: CurrentUser
    ) -> Optional[Document]:
        return (
            db.query(Document)
            .filter(
                Document.paper_id == paper_id,
                Document.user_id == user.id,
                Document.kind == DocumentKind.MAIN.value,
            )
            .first()
        )

    def get_or_create_main_for_paper(
        self,
        db: Session,
        *,
        paper_id: UUID | str,
        user: CurrentUser,
        default_content: str = "",
        default_title: str = "Untitled",
    ) -> Document:
        """Idempotent: returns existing MAIN doc, creates one if missing.

        `default_content` and `default_title` only apply on first create —
        they're never written over an existing doc. Used by the GET endpoint
        and the agent's `read_main_doc` tool to seed an empty doc with the
        paper title as an H1 instead of leaving it blank.

        The partial unique index makes the create-side race-safe; a duplicate
        insert raises 23505 and we recover by re-reading.
        """
        doc = self.get_main_for_paper(db, paper_id=paper_id, user=user)
        if doc is not None:
            return doc

        try:
            doc = Document(
                user_id=user.id,
                paper_id=paper_id,
                kind=DocumentKind.MAIN.value,
                title=default_title,
                content=default_content,
                revision=1,
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
            return doc
        except Exception as e:
            db.rollback()
            # Lost the race against another concurrent create — the existing
            # row is now visible.
            existing = self.get_main_for_paper(db, paper_id=paper_id, user=user)
            if existing is not None:
                return existing
            logger.error(
                "Failed to create MAIN doc for paper %s user %s: %s",
                paper_id,
                user.id,
                e,
                exc_info=True,
            )
            raise

    def update_with_revision_check(
        self,
        db: Session,
        *,
        doc: Document,
        content: str,
        expected_revision: int,
        user: CurrentUser,
    ) -> Document:
        """Atomic `update if revision matches; else raise`.

        Postgres handles this in a single UPDATE … WHERE id = ? AND revision = ?
        and we check rowcount. The revision is bumped server-side so the
        caller never has to compute it.
        """
        if doc.user_id != user.id:
            # Don't expose existence of other users' docs through error
            # messages. The route layer handles the 404.
            raise PermissionError("not your document")

        sanitized = sanitize_for_postgres(content)

        new_revision = int(doc.revision) + 1
        result = (
            db.query(Document)
            .filter(
                Document.id == doc.id,
                Document.revision == expected_revision,
            )
            .update(
                {
                    Document.content: sanitized,
                    Document.revision: new_revision,
                },
                synchronize_session=False,
            )
        )
        if result == 0:
            db.rollback()
            db.refresh(doc)
            raise RevisionMismatch(
                current_revision=int(doc.revision),
                current_content=str(doc.content or ""),
            )

        db.commit()
        db.refresh(doc)
        return doc


document_crud = DocumentCRUD(Document)
