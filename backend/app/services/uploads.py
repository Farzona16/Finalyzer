"""Upload checks (S9): extension, magic bytes, zip-bomb guard, size. Pure functions.

Nothing here executes or opens the content beyond reading ZIP directory entries. A file name
is only used to find its extension and as a display label; it never becomes a path.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath, PureWindowsPath

from app.config import BackendSettings
from app.errors import AppError

TEXT_SUFFIXES = (".csv", ".tsv", ".txt")
EXCEL_SUFFIXES = (".xlsx",)
ZIP_MAGIC = b"PK\x03\x04"
# Signatures of common non-table files. A table renamed to .csv must not be one of these.
BINARY_MAGICS = (
    ZIP_MAGIC,
    b"PK\x05\x06",
    b"%PDF",
    b"MZ",
    b"\x7fELF",
    b"\xd0\xcf\x11\xe0",  # old Office / OLE
    b"\x1f\x8b",  # gzip
    b"Rar!",
    b"7z\xbc\xaf",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"SQLite format 3",
)
MAX_ZIP_ENTRIES = 2000
_NAME_CLEANUP = re.compile(r"[\x00-\x1f\x7f]+")


def suffix_of(filename: str) -> str:
    """Lower-case extension of the last path component, whatever separator the client used."""
    base = PureWindowsPath(PurePosixPath(filename).name).name
    return PurePosixPath(base).suffix.lower()


def display_name(filename: str, fallback: str = "dataset") -> str:
    """A short, printable label from a file name (no directories, no control characters)."""
    base = PureWindowsPath(PurePosixPath(filename).name).name
    stem = _NAME_CLEANUP.sub("", PurePosixPath(base).stem).strip()
    return (stem or fallback)[:160]


def check_text_file(data: bytes) -> None:
    """A CSV must look like text: no binary signature, no NUL bytes."""
    if data.startswith(BINARY_MAGICS) or b"\x00" in data[:8192]:
        raise AppError("upload_unsafe", extra={"why": "not_text"})


def check_excel_file(data: bytes, settings: BackendSettings) -> None:
    """An XLSX must be a real ZIP with Office parts, and must not expand absurdly (zip bomb)."""
    if not data.startswith(ZIP_MAGIC):
        raise AppError("upload_invalid", extra={"why": "not_xlsx"})
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise AppError("upload_invalid", extra={"why": "bad_zip"}) from exc
    names = {entry.filename for entry in entries}
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise AppError("upload_invalid", extra={"why": "not_xlsx"})
    if len(entries) > MAX_ZIP_ENTRIES:
        raise AppError("upload_unsafe", extra={"why": "too_many_parts"})
    declared = sum(entry.file_size for entry in entries)
    if declared > settings.max_uncompressed_mb * 1024 * 1024:
        raise AppError("upload_unsafe", extra={"why": "expands_too_much"})
    if declared > settings.max_zip_expansion * max(len(data), 1):
        raise AppError("upload_unsafe", extra={"why": "expands_too_much"})


def validate_upload(filename: str, data: bytes, settings: BackendSettings) -> None:
    """Raise an ``AppError`` unless the file may be ingested."""
    suffix = suffix_of(filename)
    if len(filename) > 255 or "\x00" in filename:
        raise AppError("upload_invalid", extra={"why": "bad_name"})
    if suffix not in TEXT_SUFFIXES + EXCEL_SUFFIXES:
        raise AppError("unsupported_media")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AppError("payload_too_large", params={"limit_mb": settings.max_upload_mb})
    if not data.strip():
        raise AppError("upload_invalid", extra={"why": "empty"})
    if suffix in EXCEL_SUFFIXES:
        check_excel_file(data, settings)
    else:
        check_text_file(data)
