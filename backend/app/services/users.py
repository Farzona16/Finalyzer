"""User administration, preferences and workspace policy. All queries are workspace-scoped (S6)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.config import BackendSettings
from app.errors import AppError
from app.models import User, Workspace
from app.security import audit_chain as audit
from app.security.passwords import PasswordService, check_password_policy
from app.services.sessions import normalize_email, revoke_all_tokens


def list_users(db: Session, workspace_id: str) -> list[User]:
    """All users of a workspace, oldest first."""
    return list(
        db.scalars(select(User).where(User.workspace_id == workspace_id).order_by(User.created_at))
    )


def get_user_in_workspace(db: Session, workspace_id: str, user_id: str) -> User:
    """Find a user inside the actor's workspace; another workspace's id looks like 'not found'."""
    user = db.get(User, user_id)
    if user is None or user.workspace_id != workspace_id:
        raise AppError("not_found")
    return user


def active_admin_count(db: Session, workspace_id: str) -> int:
    """Number of active administrators in a workspace."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.workspace_id == workspace_id, User.role == "admin", User.is_active.is_(True)
            )
        )
        or 0
    )


def create_user(
    db: Session,
    passwords: PasswordService,
    actor: User,
    *,
    email: str,
    display_name: str,
    role: str,
    password: str,
) -> User:
    """Create a user in the actor's workspace."""
    email = normalize_email(email)
    check_password_policy(password, email)
    user = User(
        workspace_id=actor.workspace_id,
        email=email,
        display_name=display_name,
        role=role,
        password_hash=passwords.hash(password),
        language=actor.workspace.language,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("email_taken") from exc
    audit.record_event(
        db,
        audit.USER_CREATED,
        workspace_id=actor.workspace_id,
        actor_user_id=actor.id,
        detail={"target": user.id, "role": role},
    )
    return user


def _guard_admin_change(
    db: Session,
    actor: User,
    target: User,
    *,
    new_role: str | None,
    new_active: bool | None,
    new_password: str | None,
) -> None:
    """Refuse self-changes and changes that would leave no active administrator.

    Your own password is changed with the current-password check, not through this route.
    """
    losing_admin = target.role == "admin" and (
        (new_role is not None and new_role != "admin") or new_active is False
    )
    changes_self = (
        new_role not in (None, target.role) or new_active is False or new_password is not None
    )
    if target.id == actor.id and changes_self:
        raise AppError("cannot_modify_self")
    if losing_admin and active_admin_count(db, actor.workspace_id) <= 1:
        raise AppError("last_admin")


def update_user(
    db: Session,
    passwords: PasswordService,
    actor: User,
    target_id: str,
    *,
    display_name: str | None,
    role: str | None,
    is_active: bool | None,
    new_password: str | None,
) -> User:
    """Apply an admin edit. Role, status or password changes end the user's sessions."""
    target = get_user_in_workspace(db, actor.workspace_id, target_id)
    _guard_admin_change(
        db, actor, target, new_role=role, new_active=is_active, new_password=new_password
    )
    old_role, sessions_end = target.role, False
    if display_name is not None:
        target.display_name = display_name
    if role is not None and role != target.role:
        target.role, sessions_end = role, True
    if is_active is not None and is_active != target.is_active:
        target.is_active, sessions_end = is_active, True
    if new_password is not None:
        check_password_policy(new_password, target.email)
        target.password_hash = passwords.hash(new_password)
        target.password_changed_at = clock.now()
        sessions_end = True
    if sessions_end:
        revoke_all_tokens(db, target)
    db.commit()
    event = audit.ROLE_CHANGE if target.role != old_role else audit.USER_UPDATED
    audit.record_event(
        db,
        event,
        workspace_id=actor.workspace_id,
        actor_user_id=actor.id,
        detail={"target": target.id, "from": old_role, "to": target.role},
    )
    return target


def delete_user(db: Session, actor: User, target_id: str) -> None:
    """Hard-delete a user (their refresh tokens go with them)."""
    target = get_user_in_workspace(db, actor.workspace_id, target_id)
    if target.id == actor.id:
        raise AppError("cannot_modify_self")
    last_admin = active_admin_count(db, actor.workspace_id) <= 1
    if target.role == "admin" and target.is_active and last_admin:
        raise AppError("last_admin")
    db.delete(target)
    db.commit()
    audit.record_event(
        db,
        audit.USER_DELETED,
        workspace_id=actor.workspace_id,
        actor_user_id=actor.id,
        detail={"target": target_id},
    )


def update_preferences(
    db: Session,
    user: User,
    *,
    language: str | None,
    theme: str | None,
    reading_level: str | None,
) -> User:
    """Store the user's own display preferences."""
    if language is not None:
        user.language = language
    if theme is not None:
        user.theme = theme
    if reading_level is not None:
        user.reading_level = reading_level
    db.commit()
    return user


def update_policy(
    db: Session,
    settings: BackendSettings,
    actor: User,
    *,
    privacy_mode: str,
    currency: str,
    language: str,
    retention_days: int | None,
) -> Workspace:
    """Change the workspace policy; the privacy mode must be one the server allows."""
    if privacy_mode not in settings.allowed_modes:
        raise AppError("privacy_mode_not_allowed")
    if retention_days is not None and not 1 <= retention_days <= 3650:
        raise AppError("validation_failed")
    workspace = actor.workspace
    previous = workspace.privacy_mode
    workspace.privacy_mode, workspace.currency = privacy_mode, currency
    workspace.language, workspace.retention_days = language, retention_days
    db.commit()
    event = audit.PRIVACY_MODE_CHANGE if previous != privacy_mode else audit.POLICY_CHANGE
    audit.record_event(
        db,
        event,
        workspace_id=workspace.id,
        actor_user_id=actor.id,
        detail={"from": previous, "to": privacy_mode, "currency": currency},
    )
    return workspace
