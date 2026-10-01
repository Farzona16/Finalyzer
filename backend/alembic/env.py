"""Alembic environment. Uses the engine given by the app, or builds one from the settings."""

from __future__ import annotations

from alembic import context
from app.config import get_settings
from app.db import make_engine
from app.models import Base
from sqlalchemy import Engine

config = context.config
target_metadata = Base.metadata


def get_engine() -> Engine:
    """The engine passed by ``app.db`` or one built from ``DATABASE_URL``."""
    engine = config.attributes.get("engine")
    return engine if engine is not None else make_engine(get_settings())


def run_migrations() -> None:
    """Run migrations in a transaction. ``render_as_batch`` lets SQLite alter tables."""
    engine = get_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
