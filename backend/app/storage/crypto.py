"""AES-256-GCM encryption of stored dataset files (S10).

Every file gets its own random 256-bit data key. The data key is wrapped (encrypted) with the
master key from ``APP_MASTER_KEY`` and stored in the file header, so rotating the master key
means re-wrapping 48 bytes per file instead of re-encrypting the data.

File layout: ``MAGIC | wrap_nonce(12) | wrapped_key(32 + 16 tag) | nonce(12) | ciphertext``.
The caller's ``context`` (storage name and file index) is authenticated but not stored, so a
file copied to another place does not decrypt.
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FZE1"
NONCE_LEN = 12
KEY_LEN = 32
WRAPPED_LEN = KEY_LEN + 16
HEADER_LEN = len(MAGIC) + NONCE_LEN + WRAPPED_LEN + NONCE_LEN
_WRAP_CONTEXT = b"finalyzer-data-key"


class DecryptionError(Exception):
    """The file is damaged, not ours, or the key is wrong. The message holds no data."""


def _check_master(master_key: bytes) -> None:
    if len(master_key) != KEY_LEN:
        raise ValueError("the master key must be 32 bytes")


def encrypt(master_key: bytes, plaintext: bytes, context: bytes) -> bytes:
    """Encrypt ``plaintext`` with a fresh data key wrapped by ``master_key``."""
    _check_master(master_key)
    data_key = AESGCM.generate_key(bit_length=256)
    wrap_nonce, nonce = os.urandom(NONCE_LEN), os.urandom(NONCE_LEN)
    wrapped = AESGCM(master_key).encrypt(wrap_nonce, data_key, _WRAP_CONTEXT)
    body = AESGCM(data_key).encrypt(nonce, plaintext, context)
    return MAGIC + wrap_nonce + wrapped + nonce + body


def _unwrap(master_key: bytes, blob: bytes) -> bytes:
    """The data key inside a blob header."""
    _check_master(master_key)
    if len(blob) < HEADER_LEN + 16 or not blob.startswith(MAGIC):
        raise DecryptionError("not an encrypted Finalyzer file")
    start = len(MAGIC)
    wrap_nonce = blob[start : start + NONCE_LEN]
    wrapped = blob[start + NONCE_LEN : start + NONCE_LEN + WRAPPED_LEN]
    try:
        return AESGCM(master_key).decrypt(wrap_nonce, wrapped, _WRAP_CONTEXT)
    except InvalidTag as exc:
        raise DecryptionError("the master key does not open this file") from exc


def decrypt(master_key: bytes, blob: bytes, context: bytes) -> bytes:
    """Decrypt a blob made by :func:`encrypt` with the same ``context``."""
    data_key = _unwrap(master_key, blob)
    nonce = blob[HEADER_LEN - NONCE_LEN : HEADER_LEN]
    try:
        return AESGCM(data_key).decrypt(nonce, blob[HEADER_LEN:], context)
    except InvalidTag as exc:
        raise DecryptionError("the file was changed or moved") from exc


def rewrap(old_master: bytes, new_master: bytes, blob: bytes) -> bytes:
    """The same file under a new master key (key rotation); the data is not touched."""
    _check_master(new_master)
    data_key = _unwrap(old_master, blob)
    wrap_nonce = os.urandom(NONCE_LEN)
    wrapped = AESGCM(new_master).encrypt(wrap_nonce, data_key, _WRAP_CONTEXT)
    start = len(MAGIC)
    return MAGIC + wrap_nonce + wrapped + blob[start + NONCE_LEN + WRAPPED_LEN :]
