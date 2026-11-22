"""File helpers: fingerprints, atomic writes, the cache layout and reading saved results back.

Cache layout, one folder per document plus one index::

    cache/
      index.json
      DOC-042/
        record.json      the record (see models.DocumentRecord)
        page-1.png       a page picture, only when one had to be made
        text.json        the text layer, pdf_text only
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from fraudcheck.ingest.models import DocumentRecord, IngestIndex, TextLayer

RECORD_NAME = "record.json"
TEXT_NAME = "text.json"
INDEX_NAME = "index.json"
#: Files Ingest writes in a document folder; anything else there is left alone.
OWNED_PATTERNS = ("page-*.png", "page-*.jpg", TEXT_NAME, ".*.tmp")

_CHUNK = 1 << 20
#: Windows refuses to replace a file that another program (OneDrive, an antivirus, a viewer)
#: has open for a moment; such a rename is tried again a few times before giving up.
_REPLACE_ATTEMPTS = 6
_REPLACE_WAIT_S = 0.25


class CacheWriteError(RuntimeError):
    """The cache folder cannot be written (disk full, no permission...): the run stops."""


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks so large files never sit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so that readers see either the old file or the new one, never half.

    Raises ``CacheWriteError`` when the file cannot be written.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            handle.write(data)
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_WAIT_S * (attempt + 1))
    except OSError as exc:
        raise CacheWriteError(f"cannot write {path} ({exc.strerror or type(exc).__name__})") from exc
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def remove_stale_files(folder: Path, keep: set[str]) -> None:
    """Delete files Ingest wrote earlier in ``folder`` that the new record no longer uses."""
    if not folder.is_dir():
        return
    for pattern in OWNED_PATTERNS:
        for path in folder.glob(pattern):
            if path.name not in keep and path.is_file():
                with contextlib.suppress(OSError):  # a locked leftover is harmless: nothing points to it
                    path.unlink()


def read_record(path: Path) -> DocumentRecord | None:
    """Load a record; ``None`` when it is missing, unreadable or written in an older shape."""
    try:
        return DocumentRecord.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError):
        return None


def read_index(path: Path) -> IngestIndex:
    """Load the index written by the last run. Raises ``FileNotFoundError`` before any run."""
    return IngestIndex.model_validate_json(path.read_bytes())


def read_text_layer(path: Path) -> TextLayer:
    return TextLayer.model_validate_json(path.read_bytes())
