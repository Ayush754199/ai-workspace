"""On-disk storage for chat uploads and generated Office files.

Bytes live outside the database (SQL storage may stay text-only); the
`ai_attachments` row is the index. Files are namespaced per user and written
atomically. Missing files raise a clear error instead of returning made-up data.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.chat import AiAttachment

MAX_STORED_BYTES = 25 * 1024 * 1024


def storage_root() -> Path:
    root = Path(os.getenv("ATTACHMENT_STORAGE_DIR", "storage/attachments"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _key(user_id: int, digest: str, filename: str) -> Path:
    extension = Path(filename).suffix.lower() or ".bin"
    safe_extension = "".join(ch for ch in extension if ch.isalnum() or ch == ".")
    return storage_root() / str(user_id) / f"{digest}{safe_extension}"


def save_bytes(user_id: int, filename: str, data: bytes) -> str:
    """Persist bytes and return the storage path (relative to the root)."""
    if not data:
        raise ValueError("Refusing to store an empty file")
    if len(data) > MAX_STORED_BYTES:
        raise ValueError("File exceeds the 25 MB storage limit")
    digest = hashlib.sha256(data).hexdigest()
    target = _key(user_id, digest, filename).resolve()
    root = storage_root().resolve()
    if root not in target.parents:
        # Defense in depth: the file name (or user id) escaped the storage root.
        raise ValueError("Refusing to store outside the attachment directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        handle, temp_path = tempfile.mkstemp(dir=target.parent, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temp_path, target)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise
    return str(target.relative_to(storage_root()))


def load_bytes(path: str | None) -> bytes | None:
    if not path:
        return None
    target = (storage_root() / path).resolve()
    root = storage_root().resolve()
    if root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target.read_bytes()


def load_attachment(record: "AiAttachment") -> bytes | None:
    return load_bytes(getattr(record, "storage_path", None))
