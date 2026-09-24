import datetime
import logging
import os
import secrets
import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.database.crud.base_crud import CRUDBase
from app.database.models import Session as DBSession
from app.database.models import User
from app.schemas.user import UserCreate, UserUpdate

logger = logging.getLogger(__name__)


def _admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _bootstrap_user_account(db: Session, *, user: User) -> User:
    """Ensure a freshly-created user listed in ADMIN_EMAILS has admin status
    (and a verified email)."""
    is_admin_email = str(user.email).lower() in _admin_emails()
    if is_admin_email and not bool(user.is_admin):
        user.is_admin = True
        user.is_email_verified = True
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


class CRUDUser(CRUDBase[User, UserCreate, UserUpdate]):
    def get_by_email(self, db: Session, *, email: str) -> Optional[User]:
        """Get a user by email."""
        return db.query(User).filter(User.email == email).first()

    def create_session(
        self,
        db: Session,
        *,
        user_id: UUID,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        expires_in_days: int = 30,
    ) -> DBSession:
        """Create a new session for a user."""
        token = secrets.token_hex(32)  # 64 characters
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=expires_in_days
        )

        session = DBSession(
            id=uuid.uuid4(),
            user_id=user_id,
            token=token,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def get_by_token(self, db: Session, *, token: str) -> Optional[DBSession]:
        """Get session by token."""
        now = datetime.datetime.now(datetime.timezone.utc)
        session = (
            db.query(DBSession)
            .filter(DBSession.token == token, DBSession.expires_at > now)
            .first()
        )
        return session

    def revoke_session(self, db: Session, *, token: str) -> bool:
        """Revoke (delete) a session."""
        session = db.query(DBSession).filter(DBSession.token == token).first()
        if session:
            db.delete(session)
            db.commit()
            return True
        return False

    def revoke_all_sessions(self, db: Session, *, user_id: UUID) -> int:
        """Revoke all sessions for a user."""
        result = db.query(DBSession).filter(DBSession.user_id == user_id).delete()
        db.commit()
        return result

    def create_email_user(
        self, db: Session, *, email: str, name: Optional[str] = None
    ) -> User:
        """Create a new user for email authentication."""
        db_obj = User(
            email=email,
            name=name,
            auth_provider="email",
            provider_user_id=email,  # Use email as provider_user_id for email auth
            is_active=True,
            is_admin=False,
            is_email_verified=False,  # Not verified initially
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return _bootstrap_user_account(db, user=db_obj)

    def update_verification_code(
        self, db: Session, *, user: User, code: str, expires_at: datetime.datetime
    ) -> User:
        """Update user's verification code and expiry."""
        user.email_verification_token = code
        user.email_verification_expires_at = expires_at
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def verify_email(self, db: Session, *, user: User) -> User:
        """Mark user's email as verified and clear verification code."""
        user.is_email_verified = True
        user.email_verification_token = None
        user.email_verification_expires_at = None
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def get_by_email_and_provider(
        self, db: Session, *, email: str, provider: str = "email"
    ) -> Optional[User]:
        """Get a user by email and specific auth provider."""
        return (
            db.query(User)
            .filter(User.email == email, User.auth_provider == provider)
            .first()
        )


user = CRUDUser(User)
