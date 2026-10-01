"""The DuckDB manager: a small LRU cache of open datasets with a memory cap.

A handle is a v2 ``Workspace`` (an in-memory DuckDB connection with the cleaned tables and the
``base``/``data`` views) plus the built ``DatasetModel``. Opening one reads the stored tables, so
an evicted dataset costs a reload, not data. DuckDB connections are not safe for concurrent
use, so each handle has a lock and callers work inside ``with cache.use(...) as handle``.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.schemas.datasets import StoredModel
from app.storage.store import DatasetStore
from core.config import Settings as CoreSettings
from core.ingest.flatten import Workspace, build_dataset, open_workspace
from core.models import DatasetModel

# A dataset bigger than this many rows is never kept next to others: the cap is a safety net
# on top of the count limit, because DuckDB copies the data into memory.
MAX_CACHED_ROWS = 6_000_000


@dataclass
class DatasetHandle:
    """An open dataset."""

    dataset_id: str
    workspace: Workspace
    model: DatasetModel
    rows: int
    lock: threading.RLock = field(default_factory=threading.RLock)
    closed: bool = False

    def close(self) -> None:
        """Release the connection (waits for a running query to finish)."""
        with self.lock:
            if not self.closed:
                self.closed = True
                self.workspace.con.close()


def open_from_store(
    store: DatasetStore, storage_name: str, stored: StoredModel, settings: CoreSettings
) -> tuple[Workspace, DatasetModel]:
    """Load the stored tables and rebuild the flat view exactly as it was chosen."""
    tables = store.load(storage_name, stored.tables)
    workspace = open_workspace(tables, stored.cleaning_report, stored.warnings, settings)
    model = build_dataset(workspace, stored.fact_table, stored.relationships, stored.roles)
    return workspace, model


class HandleCache:
    """Least-recently-used cache of open datasets."""

    def __init__(
        self,
        store: DatasetStore,
        core_settings: Callable[[], CoreSettings],
        max_open: int,
        max_rows: int = MAX_CACHED_ROWS,
    ) -> None:
        self._store = store
        self._core_settings = core_settings
        self._max_open = max(1, max_open)
        self._max_rows = max_rows
        self._handles: OrderedDict[str, DatasetHandle] = OrderedDict()
        self._lock = threading.Lock()

    def open_ids(self) -> list[str]:
        """Dataset ids currently open, least recently used first."""
        with self._lock:
            return list(self._handles)

    def _get(self, dataset_id: str, storage_name: str, stored: StoredModel) -> DatasetHandle:
        with self._lock:
            handle = self._handles.get(dataset_id)
            if handle is not None and not handle.closed:
                self._handles.move_to_end(dataset_id)
                return handle
            workspace, model = open_from_store(
                self._store, storage_name, stored, self._core_settings()
            )
            handle = DatasetHandle(dataset_id, workspace, model, model.row_count)
            self._handles[dataset_id] = handle
            self._evict(keep=dataset_id)
            return handle

    def _evict(self, keep: str) -> None:
        """Drop the oldest handles while there are too many or they hold too many rows."""
        while len(self._handles) > 1:
            total = sum(h.rows for h in self._handles.values())
            if len(self._handles) <= self._max_open and total <= self._max_rows:
                return
            oldest = next(iter(self._handles))
            if oldest == keep:
                return
            self._handles.pop(oldest).close()

    @contextmanager
    def use(
        self, dataset_id: str, storage_name: str, stored: StoredModel
    ) -> Iterator[DatasetHandle]:
        """Open (or reuse) a dataset and hold its lock while the caller works."""
        while True:
            handle = self._get(dataset_id, storage_name, stored)
            with handle.lock:
                if handle.closed:  # evicted between lookup and lock: open again
                    continue
                yield handle
                return

    def drop(self, dataset_id: str) -> None:
        """Forget and close one dataset (after a model change or a delete)."""
        with self._lock:
            handle = self._handles.pop(dataset_id, None)
        if handle is not None:
            handle.close()

    def close_all(self) -> None:
        """Close everything (app shutdown)."""
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            handle.close()
