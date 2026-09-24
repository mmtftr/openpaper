"""POST /api/references/resolve — citation hover cards (batch, cache-first)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import get_required_user
from app.database.database import get_db
from app.references.schemas import ResolveReferencesRequest, ResolveReferencesResponse
from app.references.service import DbReferenceStore, load_library, resolve_entries
from app.schemas.user import CurrentUser

reference_router = APIRouter()


@reference_router.post("/resolve")
async def resolve_references(
    body: ResolveReferencesRequest,
    refresh: bool = Query(
        False, description="Ignore the cache and look the (single) entry up again"
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_required_user),
) -> ResolveReferencesResponse:
    """Resolve bibliography entries to papers / web pages, by the caller's keys.

    Cached results come back immediately; misses are looked up concurrently
    within a time budget. Keys in `pending` were not resolved in time or hit
    a temporary failure — ask again later.
    """
    if refresh and len(body.entries) != 1:
        raise HTTPException(400, detail="refresh=true takes exactly one entry")
    batch = await resolve_entries(
        body.entries,
        store=DbReferenceStore(db),
        library=load_library(db, current_user.id),
        refresh=refresh,
    )
    return ResolveReferencesResponse(results=batch.results, pending=batch.pending)
