"""The ORM models, one module per domain, all on one `Base`.

Importing this package registers every table — the ingest v2 tables
(`app.ingest.models`) and the reference cache (`app.references.models`)
included — so alembic and the mapper see the whole schema.
"""

from app.database.models.annotation import Annotation, Highlight, HighlightType
from app.database.models.base import Base
from app.database.models.conversation import (
    ConversableType,
    Conversation,
    Message,
    RoleType,
)
from app.database.models.discover import DiscoverSearch
from app.database.models.document import Document, DocumentKind
from app.database.models.paper import Paper, PaperStatus, PaperTag, PaperTagAssociation
from app.database.models.project import Project, ProjectPaper
from app.database.models.repo import PaperRepo, RepoStatus
from app.database.models.settings import ModelSlot
from app.database.models.user import AuthProvider, Session, User

# Tables that live next to their feature but on the same `Base`. Imported
# last: both import `Base` from `app.database.models.base`.
from app.ingest import models as _ingest_models  # noqa: E402, F401, I001
from app.references import models as _references_models  # noqa: E402, F401

__all__ = [
    "Annotation",
    "AuthProvider",
    "Base",
    "ConversableType",
    "Conversation",
    "DiscoverSearch",
    "Document",
    "DocumentKind",
    "Highlight",
    "HighlightType",
    "Message",
    "ModelSlot",
    "Paper",
    "PaperRepo",
    "PaperStatus",
    "PaperTag",
    "PaperTagAssociation",
    "Project",
    "ProjectPaper",
    "RepoStatus",
    "RoleType",
    "Session",
    "User",
]
