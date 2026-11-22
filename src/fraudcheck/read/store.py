"""Where readings are saved: ``cache/<id>/reading.json``, next to the record of step 1."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_text
from fraudcheck.read.models import Reading

READING_NAME = "reading.json"


def reading_path(ingest: IngestSettings, doc_id: str) -> Path:
    return ingest.cache_path / doc_id / READING_NAME


def load_reading(ingest: IngestSettings, doc_id: str) -> Reading | None:
    """The saved reading of a document, or ``None`` when it was never read (or the file is damaged)."""
    try:
        return Reading.model_validate_json(reading_path(ingest, doc_id).read_bytes())
    except (OSError, ValueError, ValidationError):
        return None


def save_reading(ingest: IngestSettings, reading: Reading) -> Path:
    path = reading_path(ingest, reading.id)
    atomic_write_text(path, reading.model_dump_json(indent=1))
    return path
