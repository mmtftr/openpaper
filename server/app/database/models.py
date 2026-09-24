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
    Index,
    Integer,
    String,
    Text,
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
    EMAIL = "email"  # For email-based authentication with passcode
    # Add more providers as needed
    # GITHUB = "github"
    # MICROSOFT = "microsoft"


# BASIC plans are not considered active subscriptions.
# They are used for users who have not yet subscribed.
class SubscriptionPlan(str, Enum):
    BASIC = "basic"
    RESEARCHER = "researcher"


# When a user has a RESEARCHER (or more advanced) subscription,
# they can have one of the following statuses.
class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    CANCELED = "canceled"
    PAST_DUE = "past_due"
    INCOMPLETE = "incomplete"
    TRIALING = "trialing"
    UNPAID = "unpaid"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    picture = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

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
    highlights = relationship(
        "Highlight", back_populates="user", cascade="all, delete-orphan"
    )
    annotations = relationship(
        "Annotation", back_populates="user", cascade="all, delete-orphan"
    )
    paper_upload_jobs = relationship(
        "PaperUploadJob", back_populates="user", cascade="all, delete-orphan"
    )

    # The associated subscription for the user.
    subscription = relationship(
        "Subscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    onboarding = relationship(
        "Onboarding",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    paper_tags = relationship(
        "PaperTag", back_populates="user", cascade="all, delete-orphan"
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


class ChatUsageEvent(Base):
    """One paid model exchange that persisted no `messages` row.

    The weekly chat-credit meter sums message characters; ephemeral endpoints
    (the code-viewer quick question) would otherwise never advance it. See
    `app.database.crud.chat_usage_crud`.
    """

    __tablename__ = "chat_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind = Column(String, nullable=False)
    chars = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ConversableType(str, Enum):
    PAPER = "paper"
    PROJECT = "project"


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
            "conversable_type = 'paper' AND conversable_id IS NOT NULL",
            name="check_conversable_paper",
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
    publish_date = Column(DateTime, nullable=True)
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

    # Supplementary materials are themselves Paper rows that point back to
    # their parent paper. Library listings filter rows where this is non-null
    # so supplementaries don't surface as standalone library items.
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

    project_papers = relationship("ProjectPaper", back_populates="paper")

    tags = relationship(
        "PaperTag",
        secondary="paper_tag_association",
        back_populates="papers",
    )

    # Self-referential link for supplementary materials. The
    # backref's remote_side wires up the parent_supplementary accessor on
    # the supplementary side back to the parent Paper row.
    supplementary_materials = relationship(
        "Paper",
        foreign_keys="Paper.supplementary_of_paper_id",
        backref=backref("parent_supplementary", remote_side="Paper.id"),
        cascade="all, delete-orphan",
        single_parent=True,
    )


class Project(Base):
    __tablename__ = "project"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    project_papers = relationship("ProjectPaper", back_populates="project")


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


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Subscription details
    plan = Column(String, nullable=False, default=SubscriptionPlan.BASIC)
    status = Column(String, nullable=False, default=SubscriptionStatus.ACTIVE)

    # Billing period
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)

    # Stripe integration fields
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)

    # Cancel at period end flag
    cancel_at_period_end = Column(Boolean, default=False)

    # Stripe Subscription Schedule ID (for deferred interval changes)
    stripe_schedule_id = Column(String, nullable=True)

    # When the subscription was canceled, if it was
    canceled_at = Column(DateTime(timezone=True), nullable=True)

    # Relationship with User
    user = relationship("User", back_populates="subscription")


class Onboarding(Base):
    __tablename__ = "onboarding"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Basic user information
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    company = Column(String, nullable=True)

    # Research fields (stored as comma-separated string)
    research_fields = Column(String, nullable=True)
    research_fields_other = Column(String, nullable=True)

    # Job titles (stored as comma-separated string)
    job_titles = Column(String, nullable=True)
    job_titles_other = Column(String, nullable=True)

    # Reading frequency
    reading_frequency = Column(String, nullable=True)

    # Referral source
    referral_source = Column(String, nullable=True)
    referral_source_other = Column(String, nullable=True)

    user = relationship("User", back_populates="onboarding")


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
