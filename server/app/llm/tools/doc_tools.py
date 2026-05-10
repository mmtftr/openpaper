"""Agent doc tools (paper-scoped, title-keyed).

The agent addresses each per-paper writing doc by its `name` (the doc title).
The MAIN doc's title is always literally "main"; NOTE docs use whatever title
the user picked. Three tools cover the surface:

- `list_docs`: enumerate every doc attached to this paper (names, kinds,
  revisions). The agent uses this to find out what's there before reading.
- `read_doc(name)`: returns `{content, revision}`, or `{error: not_found}`
  when no doc by that name exists. Always pair with a write.
- `write_doc(name, content, expected_revision?)`: replaces the doc's content.
  If no doc by that name exists, one is created (NOTE kind, except `name=main`
  which routes through the MAIN seed path so the partial unique index is
  honored). When the doc exists, `expected_revision` is required and is
  checked atomically — stale writes return `{error: revision_mismatch, ...}`
  so the model can re-read and merge in its next turn.

CRUD helpers are reused so revision semantics match the editor's PUT endpoint
exactly — there's only one source of truth for "what counts as a stale write".
"""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from app.database.crud.document_crud import RevisionMismatch, document_crud
from app.database.crud.paper_crud import paper_crud
from app.database.models import DocumentKind
from app.schemas.user import CurrentUser
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Same hard cap as the PUT endpoint. Surfaced in the description so the model
# doesn't try multi-MB writes.
MAX_DOC_CONTENT_BYTES = 1_000_000

# The MAIN doc is always addressed as "main" by the agent.
MAIN_DOC_NAME = "main"


list_docs_function = {
    "name": "list_docs",
    "description": (
        "List every writing doc attached to THIS paper (the user's notes, "
        "NOT the paper itself). Returns an array of {name, kind, revision, "
        "updated_at}. The MAIN doc is always present with name='main'; NOTE "
        "docs use whatever title the user gave them. Call this first when "
        "you don't know what docs exist."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


read_doc_function = {
    "name": "read_doc",
    "description": (
        "Read a writing doc on THIS paper by name. The MAIN doc is named "
        "'main'; other names come from list_docs. Returns "
        "{name, content, revision} on hit, or {error: 'not_found', name} "
        "when no doc by that name exists — surface that to the user instead "
        "of guessing a different name. Always read before write_doc on an "
        "existing doc, since the revision is required for the optimistic lock."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Doc name. Use 'main' for the user's primary writeup. "
                    "Use any name from list_docs for NOTE docs."
                ),
            },
        },
        "required": ["name"],
    },
}


write_doc_function = {
    "name": "write_doc",
    "description": (
        "Replace the content of a writing doc on THIS paper, addressed by "
        "name. If no doc by that name exists, one is created (the MAIN doc "
        "for name='main', otherwise a NOTE) and `expected_revision` is "
        "ignored — the response includes the new revision. When the doc "
        "already exists, pass `expected_revision` from the most recent "
        "read_doc; on revision_mismatch the tool returns "
        "{error: 'revision_mismatch', current_revision, current_content} "
        "instead of writing — re-read, merge your intended changes with "
        "the user's, and try again. Don't retry more than twice; surface "
        "the conflict to the user instead. Hard cap: 1MB of content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Doc name. 'main' addresses the user's primary writeup. "
                    "Any other string creates or updates a NOTE doc with "
                    "that title."
                ),
            },
            "content": {
                "type": "string",
                "description": "New full markdown content. Max 1MB.",
            },
            "expected_revision": {
                "type": "integer",
                "description": (
                    "Revision integer returned by the most recent read_doc. "
                    "Required when updating an existing doc; ignored when "
                    "the doc is being created."
                ),
            },
        },
        "required": ["name", "content"],
    },
}


def _coerce_paper_uuid(paper_id: str) -> UUID:
    try:
        return UUID(str(paper_id))
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid paper_id: {paper_id!r}") from e


def _normalize_name(name: str) -> str:
    return (name or "").strip()


def list_docs(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
) -> Dict[str, Any]:
    """Tool entry: enumerate docs for this paper.

    Auto-creates the MAIN doc on first call so the list is never empty for a
    paper the user can access — matches the GET / endpoint.
    """
    paper_uuid = _coerce_paper_uuid(paper_id)
    paper = paper_crud.get(db, id=paper_uuid, user=current_user)
    if not paper:
        return {"error": "paper not found"}

    paper_title = str(getattr(paper, "title", "") or "").strip()
    default_content = f"# {paper_title}\n\n" if paper_title else ""
    document_crud.get_or_create_main_for_paper(
        db,
        paper_id=paper_uuid,
        user=current_user,
        default_content=default_content,
        default_title=MAIN_DOC_NAME,
    )

    docs = document_crud.list_for_paper(
        db, paper_id=paper_uuid, user=current_user
    )
    return {
        "docs": [
            {
                "name": str(d.title or ""),
                "kind": str(d.kind),
                "revision": int(d.revision),
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in docs
        ]
    }


def read_doc(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
    name: str,
) -> Dict[str, Any]:
    """Tool entry: fetch a doc by name. Returns {error: not_found} when no
    such doc exists — the agent should pass that through to the user rather
    than retry with a near-match name."""
    name_clean = _normalize_name(name)
    if not name_clean:
        return {"error": "name cannot be empty"}

    paper_uuid = _coerce_paper_uuid(paper_id)
    paper = paper_crud.get(db, id=paper_uuid, user=current_user)
    if not paper:
        return {"error": "paper not found"}

    # Auto-seed MAIN so read_doc('main') works on a fresh paper without a
    # prior list_docs / editor visit.
    if name_clean == MAIN_DOC_NAME:
        paper_title = str(getattr(paper, "title", "") or "").strip()
        default_content = f"# {paper_title}\n\n" if paper_title else ""
        doc = document_crud.get_or_create_main_for_paper(
            db,
            paper_id=paper_uuid,
            user=current_user,
            default_content=default_content,
            default_title=MAIN_DOC_NAME,
        )
        return {
            "name": str(doc.title or ""),
            "content": str(doc.content or ""),
            "revision": int(doc.revision),
        }

    doc = document_crud.get_by_name_for_paper(
        db, paper_id=paper_uuid, user=current_user, name=name_clean
    )
    if doc is None:
        return {"error": "not_found", "name": name_clean}
    return {
        "name": str(doc.title or ""),
        "content": str(doc.content or ""),
        "revision": int(doc.revision),
    }


def write_doc(
    *,
    paper_id: str,
    current_user: CurrentUser,
    db: Session,
    name: str,
    content: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Tool entry: optimistically replace a doc's content, or create it if
    missing.

    Auto-create path bypasses the lock check — there's nothing to race against
    at revision 1. Update path requires `expected_revision` so the agent can't
    silently clobber concurrent user edits.
    """
    if not isinstance(content, str):
        return {"error": "content must be a string"}
    if len(content.encode("utf-8")) > MAX_DOC_CONTENT_BYTES:
        return {
            "error": "content_too_large",
            "max_bytes": MAX_DOC_CONTENT_BYTES,
        }

    name_clean = _normalize_name(name)
    if not name_clean:
        return {"error": "name cannot be empty"}

    paper_uuid = _coerce_paper_uuid(paper_id)
    paper = paper_crud.get(db, id=paper_uuid, user=current_user)
    if not paper:
        return {"error": "paper not found"}

    # Resolve target doc (creating it if absent).
    if name_clean == MAIN_DOC_NAME:
        paper_title = str(getattr(paper, "title", "") or "").strip()
        default_content = f"# {paper_title}\n\n" if paper_title else ""
        doc = document_crud.get_or_create_main_for_paper(
            db,
            paper_id=paper_uuid,
            user=current_user,
            default_content=default_content,
            default_title=MAIN_DOC_NAME,
        )
        created = doc.revision == 1 and (doc.content or "") == default_content
    else:
        doc = document_crud.get_by_name_for_paper(
            db, paper_id=paper_uuid, user=current_user, name=name_clean
        )
        if doc is None:
            doc = document_crud.create_note_for_paper(
                db, paper_id=paper_uuid, user=current_user, title=name_clean
            )
            created = True
        else:
            created = False

    if created:
        # Fresh row — bypass the lock check (nothing to race against).
        try:
            updated = document_crud.update_with_revision_check(
                db,
                doc=doc,
                content=content,
                expected_revision=int(doc.revision),
                user=current_user,
            )
        except RevisionMismatch as e:
            # Should be unreachable for a freshly-created row, but surface it
            # explicitly rather than masking a real concurrent insert.
            return {
                "error": "revision_mismatch",
                "current_revision": e.current_revision,
                "current_content": e.current_content,
            }
        return {
            "name": str(updated.title or ""),
            "revision": int(updated.revision),
            "created": True,
        }

    # Update path — require expected_revision.
    if expected_revision is None:
        return {
            "error": "expected_revision_required",
            "message": (
                f"Doc '{name_clean}' exists; call read_doc first and pass "
                "the revision it returns."
            ),
        }
    try:
        expected = int(expected_revision)
    except (TypeError, ValueError):
        return {"error": "expected_revision must be an integer"}

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
        # Defensive — get_by_name_for_paper / get_or_create_main_for_paper
        # already filter by user.
        return {"error": "not your document"}

    return {
        "name": str(updated.title or ""),
        "revision": int(updated.revision),
        "created": False,
    }


# Re-exported for callers that import the kind enum alongside these tools.
__all__ = [
    "MAIN_DOC_NAME",
    "MAX_DOC_CONTENT_BYTES",
    "DocumentKind",
    "list_docs",
    "list_docs_function",
    "read_doc",
    "read_doc_function",
    "write_doc",
    "write_doc_function",
]
