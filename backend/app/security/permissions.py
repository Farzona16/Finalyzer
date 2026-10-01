"""The role matrix (S6). Every authorization decision reads this one file."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Roles inside a workspace (decision D5)."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(StrEnum):
    """What a request needs. Routes ask for a permission, never for a role name."""

    READ = "read"  # dashboards, insights, Ask, privacy page
    UPLOAD = "upload"  # upload, build, delete own workspace datasets
    EDIT_KPIS = "edit_kpis"  # KPI manager and tile layout
    MANAGE_USERS = "manage_users"
    MANAGE_POLICY = "manage_policy"
    VIEW_AUDIT = "view_audit"
    SEE_SQL = "see_sql"  # Expert view of generated SQL


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset({Permission.READ}),
    Role.ANALYST: frozenset(
        {Permission.READ, Permission.UPLOAD, Permission.EDIT_KPIS, Permission.SEE_SQL}
    ),
    Role.ADMIN: frozenset(Permission),
}


def has_permission(role: str, permission: Permission) -> bool:
    """True when ``role`` may do ``permission``. Unknown roles may do nothing."""
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return False
