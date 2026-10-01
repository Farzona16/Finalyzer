"""Authentication endpoints (`/api/v1/auth/...`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import BackendSettings
from app.deps import (
    client_ip,
    current_user,
    get_app_settings,
    get_db,
    get_passwords,
    get_services,
    limit,
)
from app.models import User
from app.schemas.auth import (
    ChangePasswordIn,
    CsrfOut,
    FirstAdminIn,
    LoginIn,
    MeOut,
    PreferencesIn,
    UserOut,
    WorkspaceOut,
)
from app.security import csrf
from app.security.cookies import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_access_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from app.security.passwords import PasswordService
from app.security.permissions import ROLE_PERMISSIONS, Role
from app.services import datasets, sessions, users
from app.services.datasets import Services

router = APIRouter(prefix="/auth", tags=["auth"])


def _put_session_cookies(
    response: Response, settings: BackendSettings, issued: sessions.IssuedSession
) -> None:
    """Attach the access and refresh cookies of a freshly issued session."""
    set_access_cookie(response, settings, issued.access_token, issued.access_expires)
    set_refresh_cookie(response, settings, issued.refresh_token)


def _me(user: User) -> MeOut:
    """Build the `me` document."""
    permissions = sorted(permission.value for permission in ROLE_PERMISSIONS[Role(user.role)])
    return MeOut(
        user=UserOut.model_validate(user),
        workspace=WorkspaceOut.model_validate(user.workspace),
        permissions=permissions,
    )


@router.get("/csrf", response_model=CsrfOut)
def get_csrf_token(
    response: Response, settings: BackendSettings = Depends(get_app_settings)
) -> CsrfOut:
    """Issue a CSRF token (cookie and body). Call it once on page load."""
    token = csrf.issue_token(settings.app_secret_key)
    set_csrf_cookie(response, settings, token)
    return CsrfOut(csrf_token=token)


@router.post(
    "/first-admin", response_model=MeOut, status_code=201, dependencies=[Depends(limit("login"))]
)
def first_admin(
    body: FirstAdminIn,
    response: Response,
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
    passwords: PasswordService = Depends(get_passwords),
) -> MeOut:
    """Create the workspace and its first administrator (only while there are no users)."""
    user = sessions.create_first_admin(
        db,
        passwords,
        settings,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        workspace_name=body.workspace_name,
    )
    _put_session_cookies(response, settings, sessions.issue_session(db, settings, user))
    return _me(user)


@router.post("/login", response_model=MeOut, dependencies=[Depends(limit("login"))])
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
    passwords: PasswordService = Depends(get_passwords),
) -> MeOut:
    """Sign in with e-mail and password."""
    user = sessions.authenticate(
        db,
        passwords,
        email=body.email,
        password=body.password,
        ip=client_ip(request, settings),
    )
    _put_session_cookies(response, settings, sessions.issue_session(db, settings, user))
    return _me(user)


@router.post("/refresh", response_model=MeOut)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
) -> MeOut:
    """Rotate the refresh token and issue a new access token."""
    issued = sessions.rotate_refresh_token(db, settings, request.cookies.get(REFRESH_COOKIE))
    _put_session_cookies(response, settings, issued)
    return _me(issued.user)


@router.post("/logout", status_code=204)
def logout(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
    services: Services = Depends(get_services),
) -> Response:
    """Sign out on every device (ephemeral datasets of this user are deleted)."""
    sessions.logout(db, user)
    datasets.purge_for_logout(services, db, user)
    out = Response(status_code=204)
    clear_auth_cookies(out, settings)
    return out


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    """The signed-in user."""
    return _me(user)


@router.patch("/me", response_model=MeOut)
def update_me(
    body: PreferencesIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MeOut:
    """Change language, theme or reading level."""
    users.update_preferences(
        db, user, language=body.language, theme=body.theme, reading_level=body.reading_level
    )
    return _me(user)


@router.post("/change-password", response_model=MeOut)
def change_password(
    body: ChangePasswordIn,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
    passwords: PasswordService = Depends(get_passwords),
) -> MeOut:
    """Change the own password. Other devices are signed out; this one gets a new session."""
    sessions.change_password(db, passwords, user, body.current_password, body.new_password)
    _put_session_cookies(response, settings, sessions.issue_session(db, settings, user))
    return _me(user)
