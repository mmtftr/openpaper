import logging
from enum import Enum
from typing import Optional, cast

from app.auth.dependencies import get_current_user
from app.database.database import get_db
from app.database.telemetry import track_event
from app.helpers.paper_search import (
    OpenAlexFilter,
    PaperSort,
    search_open_alex,
)
from app.schemas.user import CurrentUser
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# API routes for effectively searching and retrieving papers from external sources

paper_search_router = APIRouter()


@paper_search_router.post("/search")
async def search_papers(
    query: str,
    page: int = 1,
    # Accept filter in the body for more complex queries
    filter: Optional[OpenAlexFilter] = None,
    sort: Optional[PaperSort] = None,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_current_user),
):
    """
    Search for papers based on the provided query.
    """
    try:
        # Perform the search operation
        results = search_open_alex(
            query, filter=filter, page=page, sort=sort.value if sort else None
        )
        track_event(
            "paper_search",
            user_id=current_user.id if current_user else None,
            properties={
                "query": query,
                "page": page,
                "sort": sort.value if sort else None,
                "results_count": len(results.results),
            },
            db=db,
        )
        return Response(
            content=results.model_dump_json(), media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error searching papers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
