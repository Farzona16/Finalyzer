"""Sessions: login, first admin, refresh rotation with reuse detection, logout (S2, S3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import clock
from app.config import BackendSettings
from app.errors import AppError
from app.models import RefreshToken, User, Workspace
from app.models.base import new_id
from app.security import audit_chain as audit
from app.security import throttle
from app.security.passwords import PasswordService, check_password_policy
from app.security.tokens import create_access_token, hash_refresh_token, new_refresh_token


@dataclass(frozen=True)
class IssuedSession:
    """Tokens ready to be put into cookies."""

    access_token: str
    access_expires: datetime
    refresh_token: str
    user: User


def normalize_email(email: str) -> str:
    """Lower-case and trim an e-mail for storage and lookup."""
    return email.strip().lower()


def issue_session(
    db: Session, settings: BackendSettings, user: User, family_id: str | None = None
) -> IssuedSession:
    """Create an access token and a new refresh token (same family when rotating)."""
    access, expires = create_access_token(settings, user)
    raw, hashed = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or new_id(),
            token_hash=hashed,
            expires_at=clock.now() + timedelta(days=settings.refresh_token_days),
        )
    )
    db.commit()
    return IssuedSession(access, expires, raw, user)


def revoke_all_tokens(db: Session, user: User) -> None:
    """Revoke every refresh token and invalidate access tokens already handed out."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    user.token_version += 1
    db.commit()


def create_first_admin(
    db: Session,
    passwords: PasswordService,
    settings: BackendSettings,
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
) -> User:
    """Create the workspace and its administrator. Allowed only while no user exists."""
    if db.scalar(select(User.id).limit(1)) is not None:
        raise AppError("first_admin_exists")
    email = normalize_email(email)
    check_password_policy(password, email)
    workspace = Workspace(
        name=workspace_name, privacy_mode=settings.privacy_mode, currency="UZS", language="uz"
    )
    user = User(
        workspace=workspace,
        email=email,
        display_name=display_name,
        role="admin",
        password_hash=passwords.hash(password),
        language="uz",
    )
    db.add_all([workspace, user])
    db.commit()
    audit.record_event(
        db,
        audit.FIRST_ADMIN,
        workspace_id=workspace.id,
        actor_user_id=user.id,
    )
    return user


def authenticate(
    db: Session, passwords: PasswordService, *, email: str, password: str, ip: str
) -> User:
    """Check credentials with lockout. Every failure gives the same generic error."""
    email = normalize_email(email)
    keys = throttle.throttle_keys(email, ip)
    throttle.ensure_not_locked(db, keys)
    user = db.scalar(select(User).where(User.email == email))
    valid = passwords.verify(user.password_hash if user else None, password)
    if not (valid and user is not None and user.is_active):
        throttle.record_failure(db, keys)
        audit.record_event(
            db,
            audit.LOGIN_FAILED,
            workspace_id=user.workspace_id if user else None,
            actor_user_id=user.id if user else None,
            detail={"account_key": keys[0][5:17]},
        )
        raise AppError("invalid_credentials")
    throttle.record_success(db, keys[0])
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(password)
    user.last_login_at = clock.now()
    audit.record_event(db, audit.LOGIN, workspace_id=user.workspace_id, actor_user_id=user.id)
    return user


def rotate_refresh_token(
    db: Session, settings: BackendSettings, raw_token: str | None
) -> IssuedSession:
    """Exchange a refresh token for a new session; a reused token revokes its whole family."""
    if not raw_token:
        raise AppError("invalid_token")
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    now = clock.now()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise AppError("invalid_token")
    # Claim the token atomically: only one caller can flip used_at from NULL.
    claimed = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        _revoke_family(db, row)
        raise AppError("invalid_token")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppError("invalid_token")
    return issue_session(db, settings, user, family_id=row.family_id)


def _revoke_family(db: Session, row: RefreshToken) -> None:
    """Reuse detected: revoke the family, log it, and make old access tokens invalid."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=clock.now())
    )
    user = db.get(User, row.user_id)
    if user is not None:
        user.token_version += 1
    db.commit()
    audit.record_event(
        db,
        audit.REFRESH_REUSE,
        workspace_id=user.workspace_id if user else None,
        actor_user_id=row.user_id,
        detail={"family": row.family_id[:8]},
    )


def logout(db: Session, user: User) -> None:
    """Sign out everywhere: revoke all refresh tokens and old access tokens (S3)."""
    revoke_all_tokens(db, user)
    audit.record_event(db, audit.LOGOUT, workspace_id=user.workspace_id, actor_user_id=user.id)


def change_password(
    db: Session, passwords: PasswordService, user: User, current: str, new: str
) -> None:
    """Change the own password after checking the current one; revokes all sessions."""
    if not passwords.verify(user.password_hash, current):
        raise AppError("invalid_credentials")
    check_password_policy(new, user.email)
    user.password_hash = passwords.hash(new)
    user.password_changed_at = clock.now()
    revoke_all_tokens(db, user)
    audit.record_event(
        db, audit.PASSWORD_CHANGE, workspace_id=user.workspace_id, actor_user_id=user.id
    )
