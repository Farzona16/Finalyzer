"""Jobs, datasets and dashboard layouts. They are filled in from phase U3 onward."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app import clock
from app.models.base import Base, UtcDateTime, new_id

JOB_STATES = ("queued", "running", "done", "failed", "interrupted")


class Job(Base):
    """A long task (ingest, autopilot, insights). Progress is streamed with SSE (D19)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message_key: Mapped[str] = mapped_column(String(80), default="")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)


class Dataset(Base):
    """An uploaded or demo dataset. ``storage_name`` is random and never taken from a filename."""

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")
    domain: Mapped[str | None] = mapped_column(String(30), nullable=True)
    storage_name: Mapped[str] = mapped_column(String(64), unique=True, default=new_id)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    model_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
    # Drives the idle purge of ephemeral datasets (S17).
    last_accessed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)


class Dashboard(Base):
    """A saved tile layout of one user for one dataset."""

    __tablename__ = "dashboards"
    __table_args__ = (UniqueConstraint("dataset_id", "user_id", name="uq_dashboards_owner"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(32))
    layout_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
