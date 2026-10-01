"""The file store for cleaned dataset tables (decision D6).

* ``ephemeral``: plain Parquet files under ``DATA_DIR/ephemeral``. They are deleted at logout,
  after the idle timeout and at every start (a new process has no live sessions).
* ``persistent``: every file is encrypted with AES-256-GCM (S10) under ``DATA_DIR/datasets``.

Names on disk are always random (``storage_name``) plus ``t<index>``: a table or file name
from the user is never part of a path (S9).
"""

from __future__ import annotations

import io
import os
import re
import shutil
from pathlib import Path

import pandas as pd

from app.config import BackendSettings, decode_master_key
from app.storage import crypto

STORAGE_NAME_PATTERN = re.compile(r"^[a-f0-9]{16,64}$")


class StorageError(Exception):
    """A stored file is missing or unreadable. The message holds no data values."""


class DatasetStore:
    """Save, load and delete the tables of datasets."""

    def __init__(self, settings: BackendSettings) -> None:
        self.persistent = settings.storage_mode == "persistent"
        self._master = decode_master_key(settings.app_master_key) if self.persistent else b""
        self.root = settings.data_dir / ("datasets" if self.persistent else "ephemeral")
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def folder(self, storage_name: str) -> Path:
        """The folder of one dataset. Refuses names that are not our own random ones."""
        if not STORAGE_NAME_PATTERN.match(storage_name):
            raise StorageError("invalid storage name")
        return self.root / storage_name

    def _file(self, storage_name: str, index: int) -> Path:
        suffix = "fze" if self.persistent else "parquet"
        return self.folder(storage_name) / f"t{index}.{suffix}"

    @staticmethod
    def _context(storage_name: str, index: int) -> bytes:
        return f"{storage_name}/t{index}".encode("ascii")

    # ------------------------------------------------------------------ write / read

    def save(self, storage_name: str, tables: dict[str, pd.DataFrame]) -> list[str]:
        """Write all tables; returns the table names in file order (``t0`` is the first)."""
        folder = self.folder(storage_name)
        folder.mkdir(parents=True, exist_ok=False, mode=0o700)
        names = list(tables)
        try:
            for index, name in enumerate(names):
                buffer = io.BytesIO()
                tables[name].to_parquet(buffer, index=False)
                data = buffer.getvalue()
                if self.persistent:
                    data = crypto.encrypt(self._master, data, self._context(storage_name, index))
                self._write_atomic(self._file(storage_name, index), data)
        except Exception:
            self.delete(storage_name)
            raise
        return names

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def load(self, storage_name: str, names: list[str]) -> dict[str, pd.DataFrame]:
        """Read the tables back, in the order they were saved."""
        tables: dict[str, pd.DataFrame] = {}
        for index, name in enumerate(names):
            path = self._file(storage_name, index)
            try:
                data = path.read_bytes()
                if self.persistent:
                    data = crypto.decrypt(self._master, data, self._context(storage_name, index))
                tables[name] = pd.read_parquet(io.BytesIO(data))
            except (OSError, crypto.DecryptionError) as exc:
                raise StorageError("a stored file is missing or cannot be decrypted") from exc
        return tables

    # ------------------------------------------------------------------ delete / list

    def delete(self, storage_name: str) -> None:
        """Remove every file of a dataset (hard delete, S17). Missing files are fine."""
        shutil.rmtree(self.folder(storage_name), ignore_errors=True)

    def stored_names(self) -> list[str]:
        """Storage names that have a folder on disk (used to find orphans)."""
        return [
            p.name for p in self.root.iterdir() if p.is_dir() and STORAGE_NAME_PATTERN.match(p.name)
        ]

    def wipe(self) -> None:
        """Delete everything (ephemeral mode at start-up)."""
        for name in self.stored_names():
            self.delete(name)

    def rotate_master_key(self, new_master: bytes) -> int:
        """Re-wrap the data key of every file under ``new_master``; returns the file count."""
        if not self.persistent:
            return 0
        count = 0
        for folder in (self.root / n for n in self.stored_names()):
            for path in sorted(folder.glob("t*.fze")):
                self._write_atomic(path, crypto.rewrap(self._master, new_master, path.read_bytes()))
                count += 1
        self._master = new_master
        return count
