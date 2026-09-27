"""The `/api/paper` routes, one module per concern:

- `library`: the paper lists (`/all`, `/active`, `/relevant`)
- `archive`: archiving / unarchiving papers (`/archive`)
- `detail`: one paper's record, edits, outline and markdown
- `supplementary`: a paper's supplementary materials
- `conversations`: a paper's chat conversations
- `delete`: deleting a paper and its files

`paper_router` carries the `/api/paper` prefix itself (FastAPI won't
nest the `""` paths under an empty prefix). Figures, the repo, ingest,
uploads and tags have their own routers under the same prefix (see
`app.main`).
"""

from fastapi import APIRouter

from app.api.paper import (
    archive,
    conversations,
    delete,
    detail,
    library,
    supplementary,
)

PREFIX = "/api/paper"

paper_router = APIRouter()
for _module in (library, archive, detail, supplementary, conversations, delete):
    paper_router.include_router(_module.router, prefix=PREFIX)
