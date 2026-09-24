import uuid
from enum import Enum
from types import NoneType

from sqlalchemy import (  # type: ignore
    ARRAY,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (  # type: ignore
    DeclarativeBase,
    backref,
    foreign,
    relationship,
    sessionmaker,
)
from sqlalchemy.sql import func

# Special notes:
# - All models inherit from the `Base` class, which provides common fields and methods.
# - The `last_accessed_at` field is automatically updated to the current timestamp
#   whenever the record is accessed. It is only present in selected models
#   (e.g., `Paper` to track when a user last interacted with a paper.)
# - This can be useful for tracking user activity and engagement with papers.
# - The `created_at` and `updated_at` fields are automatically managed by SQLAlchemy
#   to record when the record was created and last updated, respectively.
# - The `to_dict` method converts the model instance to a dictionary, making it easier
#   to serialize the model for APIs or other uses.


class Base(DeclarativeBase):
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.id}>"

    def to_dict(self):
        """
        Convert the SQLAlchemy model instance to a dictionary.
        """

        def _to_json_friendly(value):
            if isinstance(value, list):
                return [_to_json_friendly(item) for item in value]
            elif isinstance(value, dict):
                return {key: _to_json_friendly(val) for key, val in value.items()}
            elif isinstance(value, (int, float, bool)):
                return value
            elif isinstance(value, NoneType):
                return None
            return str(value)

        return {
            column.name: _to_json_friendly(getattr(self, column.name))
            for column in self.__table__.columns
        }


class AuthProvider(str, Enum):
    GOOGLE = "google"
    EMAIL = "email"  # For email-based authentication with passcode
    # Add more providers as needed
    # GITHUB = "github"
    # MICROSOFT = "microsoft"


class ProjectRoles(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    picture = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False, nullable=False)

    # OAuth related fields
    auth_provider = Column(String, nullable=False)
    provider_user_id = Column(String, nullable=False, index=True)

    # Email authentication fields
    is_email_verified = Column(
        Boolean, default=False, nullable=False
    )  # Track if email is verified
    email_verification_token = Column(String, nullable=True)  # Store 6-digit code
    email_verification_expires_at = Column(
        DateTime(timezone=True), nullable=True
    )  # Expiry time

    # Optional profile information
    locale = Column(String, nullable=True)

    papers = relationship("Paper", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    messages = relationship(
        "Message", back_populates="user", cascade="all, delete-orphan"
    )
    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )
    paper_notes = relationship(
        "PaperNote", back_populates="user", cascade="all, delete-orphan"
    )
    highlights = relationship(
        "Highlight", back_populates="user", cascade="all, delete-orphan"
    )
    annotations = relationship(
        "Annotation", back_populates="user", cascade="all, delete-orphan"
    )
    audio_overview_jobs = relationship(
        "AudioOverviewJob", back_populates="user", cascade="all, delete-orphan"
    )
    paper_upload_jobs = relationship(
        "PaperUploadJob", back_populates="user", cascade="all, delete-orphan"
    )

    project_roles = relationship("ProjectRole", back_populates="user")
    paper_tags = relationship(
        "PaperTag", back_populates="user", cascade="all, delete-orphan"
    )
    invitations = relationship(
        "ProjectRoleInvitation", back_populates="inviter", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)

    user = relationship("User", back_populates="sessions")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RoleType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class PaperUploadJob(Base):
    __tablename__ = "paper_upload_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(String, nullable=False, default=JobStatus.PENDING)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    task_id = Column(String, nullable=True)  # For tracking task in Celery
    # When set, this upload is producing a supplementary material for the
    # referenced parent paper. The webhook stamps the resulting Paper row's
    # supplementary_of_paper_id with this value. No FK here — the job is
    # transient; the durable link lives on Paper.
    supplementary_of_paper_id = Column(UUID(as_uuid=True), nullable=True)

    user = relationship("User", back_populates="paper_upload_jobs")


class PaperStatus(str, Enum):
    todo = "todo"
    reading = "reading"
    completed = "completed"


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)

    # References from the paper. Key 'citations' maps to list of ResponseCitation dicts
    references = Column(JSONB, nullable=True)

    bucket = Column(JSONB, nullable=True)  # For any additional attributes
    sequence = Column(Integer, nullable=False)  # To maintain message order
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    user = relationship("User", back_populates="messages")
    conversation = relationship("Conversation", back_populates="messages")


class ConversableType(str, Enum):
    PAPER = "paper"
    PROJECT = "project"
    EVERYTHING = (
        "everything"  # For conversations that are across the user's entire library
    )


def generic_relationship(type_col_name, id_col_name):
    """Returns a property that emulates a generic relationship."""

    def getter(self):
        """Get the related object."""
        # Get the type and ID from the instance
        type_name = getattr(self, type_col_name)
        id_val = getattr(self, id_col_name)
        if type_name is None or id_val is None:
            return None

        # Get the session and find the object
        session = sessionmaker.object_session(self)
        if not session:
            # Cannot function without a session
            return None

        # Dynamically get the parent class from the Base's registry
        parent_class = self.registry.class_mapper(type_name).class_
        return session.get(parent_class, id_val)

    def setter(self, value):
        """Set the related object."""
        # Get the type and ID from the object being assigned
        type_name = value.__tablename__ if value else None
        id_val = value.id if value else None

        setattr(self, type_col_name, type_name)
        setattr(self, id_col_name, id_val)

    return property(getter, setter)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=True)  # Optional conversation title
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Polymorphic Columns
    conversable_id = Column(UUID(as_uuid=True), nullable=True)
    conversable_type = Column(String, nullable=False, default=ConversableType.PAPER)
    conversable = generic_relationship("conversable_type", "conversable_id")

    # Specific relationship for papers
    paper = relationship(
        "Paper",
        primaryjoin=lambda: and_(
            foreign(Conversation.conversable_id) == Paper.id,
            Conversation.conversable_type == ConversableType.PAPER.value,
        ),
        viewonly=True,
    )

    user = relationship("User", back_populates="conversations")

    messages = relationship(
        "Message",
        back_populates="conversation",
        order_by=Message.sequence,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "(conversable_type = 'paper' AND conversable_id IS NOT NULL) OR "
            "(conversable_type = 'everything' AND conversable_id IS NULL)",
            name="check_conversable_consistency",
        ),
    )


class PaperTag(Base):
    __tablename__ = "paper_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    color = Column(String, nullable=True)  # Optional color for the tag
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    user = relationship("User", back_populates="paper_tags")
    papers = relationship(
        "Paper",
        secondary="paper_tag_association",
        back_populates="tags",
    )


class PaperTagAssociation(Base):
    __tablename__ = "paper_tag_association"

    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id = Column(
        UUID(as_uuid=True),
        ForeignKey("paper_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Paper(Base):
    __tablename__ = "papers"

    # Define the GIN index for full-text search
    __table_args__ = (
        Index("ix_papers_ts_vector", "ts_vector", postgresql_using="gin"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # we can change the default to TODO once we have some kind of bulk paper upload? for now, every upload automatically converts to reading
    status = Column(String, nullable=False, default=PaperStatus.reading)
    file_url = Column(String, nullable=False)
    preview_url = Column(String, nullable=True)
    s3_object_key = Column(String, nullable=True)
    authors = Column(ARRAY(String), nullable=True)
    title = Column(Text, nullable=True)
    abstract = Column(Text, nullable=True)
    institutions = Column(ARRAY(String), nullable=True)
    keywords = Column(ARRAY(String), nullable=True)
    summary = Column(Text, nullable=True)
    summary_citations = Column(JSONB, nullable=True)
    publish_date = Column(DateTime, nullable=True)
    starter_questions = Column(ARRAY(String), nullable=True)
    raw_content = Column(Text, nullable=True)
    ts_vector = Column(TSVECTOR, nullable=True)
    page_offset_map = Column(
        JSONB, nullable=True
    )  # Maps page numbers to text offsets. Useful for re-annotation.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_accessed_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    upload_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("paper_upload_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Cached presigned URL fields
    cached_presigned_url = Column(String, nullable=True)
    presigned_url_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Optional fields for sharing
    is_public = Column(Boolean, default=False)
    share_id = Column(String, unique=True, nullable=True, index=True)

    # Additional metadata
    doi = Column(String, nullable=True)  # Digital Object Identifier
    journal = Column(String, nullable=True)
    publisher = Column(String, nullable=True)
    attempted_metadata_at = Column(DateTime(timezone=True), nullable=True)

    size_in_kb = Column(Integer, nullable=True)  # Size of the paper file in KB

    # OCR pipeline. parser is "mistral" | "pymupdf" — selects the chat
    # context-mode surface for this paper. ocr is the per-page jsonb (Mistral
    # response with image_base64 stripped); used by the agentic chat tools.
    parser = Column(Text, nullable=True)
    ocr = Column(JSONB, nullable=True)
    generated_outline = Column(JSONB, nullable=True)
    figure_count = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=True)

    # Some papers can be forked/duplicated from other papers (across users). To handle this, we store the parent paper ID of the original paper.
    parent_paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Supplementary materials are themselves Paper rows that point back to
    # their parent paper. Distinct from parent_paper_id (forks). Library
    # listings filter rows where this is non-null so supplementaries don't
    # surface as standalone library items.
    supplementary_of_paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user = relationship("User", back_populates="papers")
    conversations = relationship(
        "Conversation",
        back_populates="paper",
        cascade="all, delete-orphan",
        primaryjoin=lambda: and_(
            Paper.id == foreign(Conversation.conversable_id),
            Conversation.conversable_type == ConversableType.PAPER.value,
        ),
    )
    paper_notes = relationship(
        "PaperNote", back_populates="paper", cascade="all, delete-orphan"
    )

    audio_overviews = relationship(
        "AudioOverview",
        cascade="all, delete-orphan",
        primaryjoin=lambda: and_(
            Paper.id == foreign(AudioOverview.conversable_id),
            AudioOverview.conversable_type == ConversableType.PAPER.value,
        ),
        overlaps="audio_overviews",
    )

    audio_overview_jobs = relationship(
        "AudioOverviewJob",
        cascade="all, delete-orphan",
        primaryjoin=lambda: and_(
            Paper.id == foreign(AudioOverviewJob.conversable_id),
            AudioOverviewJob.conversable_type == ConversableType.PAPER.value,
        ),
        overlaps="audio_overview_jobs",
    )

    paper_images = relationship(
        "PaperImage", back_populates="paper", cascade="all, delete-orphan"
    )

    project_papers = relationship("ProjectPaper", back_populates="paper")

    tags = relationship(
        "PaperTag",
        secondary="paper_tag_association",
        back_populates="papers",
    )

    # Self-referential link for supplementary materials. Disambiguated from
    # the existing parent_paper_id (fork) link via foreign_keys=. The
    # backref's remote_side wires up the parent_supplementary accessor on
    # the supplementary side back to the parent Paper row.
    supplementary_materials = relationship(
        "Paper",
        foreign_keys="Paper.supplementary_of_paper_id",
        backref=backref("parent_supplementary", remote_side="Paper.id"),
        cascade="all, delete-orphan",
        single_parent=True,
    )


class PaperPassage(Base):
    __tablename__ = "paper_passages"

    __table_args__ = (
        UniqueConstraint("paper_id", "start_line"),
        Index("ix_paper_passages_ts_vector", "ts_vector", postgresql_using="gin"),
    )

    id = Column(BigInteger, Identity(always=True), primary_key=True)
    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    ts_vector = Column(TSVECTOR, nullable=True)

    paper = relationship("Paper")


class Project(Base):
    __tablename__ = "project"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    project_roles = relationship("ProjectRole", back_populates="project")
    project_papers = relationship("ProjectPaper", back_populates="project")

    audio_overviews = relationship(
        "AudioOverview",
        cascade="all, delete-orphan",
        primaryjoin=lambda: and_(
            Project.id == foreign(AudioOverview.conversable_id),
            AudioOverview.conversable_type == ConversableType.PROJECT.value,
        ),
        overlaps="audio_overviews",
    )

    audio_overview_jobs = relationship(
        "AudioOverviewJob",
        cascade="all, delete-orphan",
        primaryjoin=lambda: and_(
            Project.id == foreign(AudioOverviewJob.conversable_id),
            AudioOverviewJob.conversable_type == ConversableType.PROJECT.value,
        ),
        overlaps="audio_overview_jobs",
    )
    invitations = relationship(
        "ProjectRoleInvitation", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectRoleInvitation(Base):
    __tablename__ = "project_role_invitations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    email = Column(String, nullable=False)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role = Column(String, nullable=False)
    invited_at = Column(DateTime(timezone=True), server_default=func.now())
    accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    inviter = relationship(
        "User", foreign_keys=[invited_by], back_populates="invitations"
    )
    project = relationship(
        "Project", back_populates="invitations", foreign_keys=[project_id]
    )


class ProjectRole(Base):
    __tablename__ = "project_role"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role = Column(String, nullable=False, default=ProjectRoles.ADMIN)

    project = relationship("Project", back_populates="project_roles")
    user = relationship("User", back_populates="project_roles")


class ProjectPaper(Base):
    """
    Association table for linking papers and projects. This is because projects can have many papers and papers can belong to many projects.
    """

    __tablename__ = "project_paper"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id = Column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )

    project = relationship("Project", back_populates="project_papers")
    paper = relationship("Paper", back_populates="project_papers")


class ProjectAudioOverview(Base):
    __tablename__ = "project_audio_overview"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), nullable=False
    )
    audio_overview_id = Column(
        UUID(as_uuid=True),
        ForeignKey("audio_overviews.id", ondelete="CASCADE"),
        nullable=False,
    )


class PaperImage(Base):
    __tablename__ = "paper_images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id = Column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    s3_object_key = Column(String, nullable=False)
    image_url = Column(String, nullable=False)
    format = Column(String, nullable=False)  # e.g., 'png', 'jpg'

    size_bytes = Column(Integer, nullable=False)  # Size of the image in bytes
    width = Column(Integer, nullable=False)  # Width of the image in pixels
    height = Column(Integer, nullable=False)  # Height of the image in pixels

    page_number = Column(
        Integer, nullable=False
    )  # Page number where the image is located
    image_index = Column(Integer, nullable=False)  # Index of the image in the paper

    caption = Column(Text, nullable=True)  # Optional caption for the image

    placeholder_id = Column(String, nullable=True)  # Placeholder ID for the image

    paper = relationship("Paper", back_populates="paper_images")


class RepoStatus(str, Enum):
    PENDING = "pending"
    INGESTING = "ingesting"
    READY = "ready"
    ERROR = "error"


class PaperRepo(Base):
    """A paper's companion GitHub repository, ingested into a local snapshot.

    One repo per paper (UNIQUE paper_id). The snapshot itself lives on disk
    under `{REPO_STORAGE_DIR}/{paper_id}/{commit_sha}/` — this row is the
    index into it plus the ingestion state machine
    (`pending` → `ingesting` → `ready` | `error`).
    """

    __tablename__ = "paper_repos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    owner = Column(String, nullable=False)
    repo = Column(String, nullable=False)
    # Resolved default branch; the SHA is what everything is pinned to.
    ref = Column(String, nullable=True)
    commit_sha = Column(String, nullable=True)

    status = Column(String, nullable=False, default=RepoStatus.PENDING.value)
    error = Column(Text, nullable=True)

    file_count = Column(Integer, nullable=True)
    total_bytes = Column(BigInteger, nullable=True)
    # `{paper_id}/{commit_sha}` under REPO_STORAGE_DIR.
    storage_prefix = Column(String, nullable=True)

    paper = relationship("Paper")


class DocumentKind(str, Enum):
    MAIN = "main"  # the paper's main writeup; exactly one per (paper_id, user_id)
    NOTE = "note"  # any other doc, paper-scoped or root-level (slice 3)


class Document(Base):
    """User-and-agent-editable markdown documents.

    Slice 1 only fills MAIN rows (one per paper per user). The schema is shaped
    for the eventual end state — paper-scoped folder trees plus root-level user
    docs — so we don't re-migrate when slice 3 lands.
    """

    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL paper_id = root-level user doc (slice 3).
    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Folder hierarchy. NULL = top of its scope (paper or root).
    parent_document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
    )

    kind = Column(String, nullable=False, default=DocumentKind.NOTE)
    title = Column(String, nullable=False, default="Untitled")
    content = Column(Text, nullable=False, default="")

    # Bumped on every successful write; used for optimistic locking against
    # concurrent agent + user edits.
    revision = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index(
            "ux_documents_main_per_paper",
            "paper_id",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'main' AND paper_id IS NOT NULL"),
        ),
        Index("ix_documents_paper_user", "paper_id", "user_id"),
        Index("ix_documents_parent", "parent_document_id"),
    )


class PaperNote(Base):
    __tablename__ = "paper_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Ensure each document has only one associated paper note
    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    user = relationship("User", back_populates="paper_notes")

    paper = relationship("Paper", back_populates="paper_notes")


class HighlightType(str, Enum):
    TOPIC = "topic"
    MOTIVATION = "motivation"
    METHOD = "method"
    EVIDENCE = "evidence"
    RESULT = "result"
    IMPACT = "impact"
    GENERAL = "general"


class Highlight(Base):
    __tablename__ = "highlights"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_id = Column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    raw_text = Column(Text, nullable=False)
    type = Column(String, nullable=True)  # HighlightType enum value)

    # Position (exact for user, hints for AI)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    page_number = Column(Integer, nullable=True)

    position = Column(JSONB, nullable=True)

    # Role
    # This can be user for user-created highlights or assistant for AI-generated highlights
    role = Column(String, nullable=False, default="user")  # 'user' or 'assistant'
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    color = Column(String, nullable=True, default="blue")

    # Relationships
    user = relationship("User", back_populates="highlights")
    annotations = relationship(
        "Annotation", back_populates="highlight", cascade="all, delete-orphan"
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The associated highlight
    highlight_id = Column(
        UUID(as_uuid=True), ForeignKey("highlights.id"), nullable=False
    )

    # The associated paper
    paper_id = Column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    content = Column(Text, nullable=False)

    # Role tracking
    role = Column(String, nullable=False, default="user")  # 'user' or 'assistant'
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Relationships
    user = relationship("User", back_populates="annotations")
    highlight = relationship("Highlight", back_populates="annotations")


class AudioOverviewJob(Base):
    __tablename__ = "audio_overview_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    conversable_id = Column(UUID(as_uuid=True), nullable=False)
    conversable_type = Column(String, nullable=False, default=ConversableType.PAPER)
    conversable = generic_relationship("conversable_type", "conversable_id")

    status = Column(String, nullable=False, default=JobStatus.PENDING)
    status_message = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="audio_overview_jobs")

    # Specific relationship for papers (viewonly)
    paper = relationship(
        "Paper",
        primaryjoin=lambda: and_(
            foreign(AudioOverviewJob.conversable_id) == Paper.id,
            AudioOverviewJob.conversable_type == ConversableType.PAPER.value,
        ),
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(conversable_type = 'paper' AND conversable_id IS NOT NULL) OR "
            "(conversable_type = 'project' AND conversable_id IS NOT NULL) OR "
            "(conversable_type = 'everything' AND conversable_id IS NULL)",
            name="check_audio_overview_job_conversable_consistency",
        ),
    )


class AudioOverview(Base):
    __tablename__ = "audio_overviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    s3_object_key = Column(
        String, nullable=False
    )  # Store the S3 object key of the wav file

    transcript = Column(Text, nullable=True)

    citations = Column(
        JSONB, nullable=True
    )  # Store citations in a JSONB format for flexibility. Typically, it would be a list of dicts with keys like `index` and `text`.

    title = Column(String, nullable=True)

    conversable_id = Column(UUID(as_uuid=True), nullable=False)
    conversable_type = Column(String, nullable=False, default=ConversableType.PAPER)
    conversable = generic_relationship("conversable_type", "conversable_id")

    # Specific relationship for papers (viewonly)
    paper = relationship(
        "Paper",
        primaryjoin=lambda: and_(
            foreign(AudioOverview.conversable_id) == Paper.id,
            AudioOverview.conversable_type == ConversableType.PAPER.value,
        ),
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(conversable_type = 'paper' AND conversable_id IS NOT NULL) OR "
            "(conversable_type = 'project' AND conversable_id IS NOT NULL) OR "
            "(conversable_type = 'everything' AND conversable_id IS NULL)",
            name="check_audio_overview_conversable_consistency",
        ),
    )


class DiscoverSearch(Base):
    __tablename__ = "discover_searches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question = Column(Text, nullable=False)
    subqueries = Column(JSONB, nullable=True)
    results = Column(JSONB, nullable=True)

    user = relationship("User")


class DataTableExtractionJob(Base):
    __tablename__ = "data_table_extraction_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    project_id = Column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), nullable=True
    )

    columns = Column(ARRAY(String), nullable=True)  # Columns to extract

    task_id = Column(String, nullable=True)  # For tracking task in Celery

    status = Column(String, nullable=False, default=JobStatus.PENDING)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    error_message = Column(Text, nullable=True)

    user = relationship("User")
    project = relationship("Project")

    # Relationship to results
    result = relationship(
        "DataTableExtractionResult",
        back_populates="job",
        uselist=False,
        cascade="all, delete-orphan",
    )


class DataTableExtractionResult(Base):
    """
    Stores the result of a data table extraction job.
    Contains the columns extracted and links to individual row results.
    """

    __tablename__ = "data_table_extraction_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=True)
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_table_extraction_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    success = Column(Boolean, nullable=False, default=True)
    columns = Column(ARRAY(String), nullable=False)  # List of column names
    row_failures = Column(
        ARRAY(UUID(as_uuid=True)), nullable=True, default=[]
    )  # List of paper IDs that failed

    job = relationship("DataTableExtractionJob", back_populates="result")
    rows = relationship(
        "DataTableRow",
        back_populates="data_table",
        cascade="all, delete-orphan",
    )


class DataTableRow(Base):
    """
    Stores a single row of extracted data for a paper.
    The 'values' field is JSONB containing: {column_name: {value: str, citations: [{text, index}]}}
    """

    __tablename__ = "data_table_rows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_table_id = Column(
        UUID(as_uuid=True),
        ForeignKey("data_table_extraction_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id = Column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    values = Column(JSONB, nullable=False, default={})
    # values schema: {
    #   "column_name": {
    #     "value": "extracted value",
    #     "citations": [{"text": "citation text", "index": 1}, ...]
    #   }
    # }

    data_table = relationship("DataTableExtractionResult", back_populates="rows")
    paper = relationship("Paper")

    # Index for efficient lookups by paper
    __table_args__ = (
        Index("ix_data_table_rows_paper_id", "paper_id"),
        Index("ix_data_table_rows_data_table_id", "data_table_id"),
    )
