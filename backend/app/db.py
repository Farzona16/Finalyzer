"""Database engine, sessions and migrations (SQLite locally, PostgreSQL on Render)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from app.config import BACKEND_ROOT, BackendSettings

ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


def make_engine(settings: BackendSettings) -> Engine:
    """Create the engine. SQLite gets foreign keys and a busy timeout."""
    url = settings.database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True)


def _sqlite_pragmas(connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """SQLite ignores foreign keys unless asked, and locks quickly without WAL."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Sessions that do not expire objects on commit (we read them after commit)."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session that commits on success and rolls back on any error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(engine: Engine, ini_path: Path = ALEMBIC_INI) -> Config:
    """Alembic config bound to an existing engine (so URLs with ``%`` need no escaping)."""
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["engine"] = engine
    return config


def upgrade_to_head(engine: Engine) -> None:
    """Apply all migrations."""
    command.upgrade(alembic_config(engine), "head")


def downgrade_to_base(engine: Engine) -> None:
    """Undo all migrations (used by the migration test)."""
    command.downgrade(alembic_config(engine), "base")
