"""Command line helpers: ``python -m app.cli <command>``.

Commands: ``generate-keys``, ``migrate``, ``create-admin``, ``verify-audit``.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import secrets
import sys

from sqlalchemy import select

from app.config import ConfigError, get_settings
from app.db import make_engine, make_session_factory, session_scope, upgrade_to_head
from app.errors import AppError
from app.models import AuditRecord, User, Workspace
from app.security import audit_chain
from app.security.passwords import PasswordService, calibrated_hasher, check_password_policy
from app.services.sessions import normalize_email


def generate_keys() -> int:
    """Print fresh values for APP_SECRET_KEY and APP_MASTER_KEY (never stored by the tool)."""
    print(f"APP_SECRET_KEY={secrets.token_urlsafe(48)}")
    print(f"APP_MASTER_KEY={base64.b64encode(secrets.token_bytes(32)).decode('ascii')}")
    return 0


def migrate() -> int:
    """Apply database migrations."""
    upgrade_to_head(make_engine(get_settings()))
    print("Database is up to date.")
    return 0


def create_admin(email: str, name: str, workspace_name: str) -> int:
    """Create an administrator (and a workspace when none exists). Asks for the password."""
    settings = get_settings()
    engine = make_engine(settings)
    upgrade_to_head(engine)
    password = getpass.getpass("Password (min. 12 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("The passwords do not match.", file=sys.stderr)
        return 1
    passwords = PasswordService(calibrated_hasher())
    factory = make_session_factory(engine)
    try:
        check_password_policy(password, email)
        with session_scope(factory) as db:
            workspace = db.scalar(select(Workspace).limit(1)) or Workspace(
                name=workspace_name, privacy_mode=settings.privacy_mode
            )
            db.add(
                User(
                    workspace=workspace,
                    email=normalize_email(email),
                    display_name=name,
                    role="admin",
                    password_hash=passwords.hash(password),
                )
            )
    except AppError as exc:
        print(f"Rejected: {exc.code}", file=sys.stderr)
        return 1
    print("Administrator created.")
    return 0


def verify_audit() -> int:
    """Verify every audit chain in the database. Exit code 1 when one is broken."""
    factory = make_session_factory(make_engine(get_settings()))
    broken = 0
    with session_scope(factory) as db:
        chains = list(db.scalars(select(AuditRecord.chain_id).distinct()))
        for chain in chains:
            result = audit_chain.verify_chain(db, chain)
            status = "ok" if result.ok else f"BROKEN at seq {result.broken_at_seq}"
            print(f"{chain}: {status} ({result.checked} records checked)")
            broken += 0 if result.ok else 1
    return 1 if broken else 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-keys", help="print new APP_SECRET_KEY and APP_MASTER_KEY")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("verify-audit", help="verify all audit chains")
    admin = sub.add_parser("create-admin", help="create an administrator")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="Administrator")
    admin.add_argument("--workspace", default="Finalyzer")
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-keys":
            return generate_keys()
        if args.command == "migrate":
            return migrate()
        if args.command == "verify-audit":
            return verify_audit()
        return create_admin(args.email, args.name, args.workspace)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
