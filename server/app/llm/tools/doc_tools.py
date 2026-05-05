"""Agent read/write of the user's main writing doc — slice 2.

The MAIN doc is the user's notes/writeup attached to a paper, not the paper
itself. Surfaced via two tools:

- `read_main_doc`: returns `{content, revision}`. Always pair with a write.
- `write_main_doc`: replaces content; gated by `expected_revision` against
  the live row. Stale writes return `{error: revision_mismatch, ...}` so
  the model can re-read and merge in its next turn.

Reuses `document_crud` so the conflict semantics match the editor's PUT
endpoint exactly — there's only one source of truth for "what counts as a
stale write".
"""

import logging
from typing import Any, Dict
from uuid import UUID

from app.database.crud.document_crud import RevisionMismatch, document_crud
from app.database.crud.paper_crud import paper_crud
from app.schemas.user import CurrentUser
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Same hard cap as the PUT endpoint. Surfaced in the description so the
# model doesn't try multi-MB writes.
MAX_DOC_CONTENT_BYTES = 1_000_000


read_main_doc_function = {
    "name": "read_main_doc",
    "description": (
        "Read the user's main writing doc for THIS paper (their notes / "
        "writeup, NOT the paper itself). Returns the current markdown plus a "
        "revision integer. ALWAYS call this before write_main_doc — the "
        "revision is required for the optimistic lock. Returns "
        "{content: '', revision: 1} when the doc is empty/new."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


write_main_doc_function = {
    "name": "write_main_doc",
    "description": (
        "Replace the user's main writing doc for this paper with new "
        "markdown content. Pass `expected_revision` from the most recent "
        "read_main_doc call. On revision mismatch the tool returns "
        "{error: 'revision_mismatch', current_revision, current_content} "
        "instead of writing — re-read, merge your intended changes with the "
        "user's, and try again. Do not retry more than twice; surface the "
        "conflict to the user instead. Hard cap: 1MB of content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "New full markdown content. Max 1MB.",
            },
            "expected_revision": {
                "type": "integer",
                "description": (
                    "Revision integer returned by the most recent read_main_doc."
                ),
            },
        },
        "required": ["content", "expected_revision"],
    },
}


def _coerce_paper_uuid(paper_id: str) -> UUID:
    try:
        return UUID(str(paper_id))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid paper_id: {paper_id!r}") from e


def _seed_defaults(paper) -> tuple[str, str]:
    """Seed values used only when creating a fresh MAIN doc.

    First-time docs open with the paper's title as an H1 so the user starts
    with a sensible scaffold rather than an empty page.
    """
    title = str(getattr(paper, "title", "") or "").strip()
    default_content = f"# {title}\n\n" if title else ""
    default_title = title or "Untitled"
    return default_content, default_title


def read_main_doc(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
) -> Dict[str, Any]:
    """Tool entry: return the current MAIN doc for this paper.

    Idempotent — creates an empty MAIN row on first call so the agent can
    write into a guaranteed-existing document.
    """
    paper_uuid = _coerce_paper_uuid(paper_id)
    paper = paper_crud.get(db, id=paper_uuid, user=current_user)
    default_content, default_title = _seed_defaults(paper) if paper else ("", "Untitled")
    doc = document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_uuid,
        user=current_user,
        default_content=default_content,
        default_title=default_title,
    )
    return {
        "document_id": str(doc.id),
        "content": str(doc.content or ""),
        "revision": int(doc.revision),
    }


def write_main_doc(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
    content: str,
    expected_revision: int,
) -> Dict[str, Any]:
    """Tool entry: optimistically replace MAIN doc content.

    Returns the new revision on success or {error: 'revision_mismatch', ...}
    on stale write. Size cap returns an error rather than raising so the
    model can shrink-and-retry without crashing the loop.
    """
    if not isinstance(content, str):
        return {"error": "content must be a string"}

    if len(content.encode("utf-8")) > MAX_DOC_CONTENT_BYTES:
        return {
            "error": "content_too_large",
            "max_bytes": MAX_DOC_CONTENT_BYTES,
        }

    try:
        expected = int(expected_revision)
    except (TypeError, ValueError):
        return {"error": "expected_revision must be an integer"}

    paper_uuid = _coerce_paper_uuid(paper_id)
    paper = paper_crud.get(db, id=paper_uuid, user=current_user)
    default_content, default_title = _seed_defaults(paper) if paper else ("", "Untitled")
    doc = document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_uuid,
        user=current_user,
        default_content=default_content,
        default_title=default_title,
    )

    try:
        updated = document_crud.update_with_revision_check(
            db,
            doc=doc,
            content=content,
            expected_revision=expected,
            user=current_user,
        )
    except RevisionMismatch as e:
        return {
            "error": "revision_mismatch",
            "current_revision": e.current_revision,
            "current_content": e.current_content,
        }
    except PermissionError:
        # Defensive — get_or_create_main_for_paper already filters by user.
        return {"error": "not your document"}

    return {
        "document_id": str(updated.id),
        "revision": int(updated.revision),
    }
