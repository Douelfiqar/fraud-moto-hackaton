"""Where inspections are saved: ``cache/<id>/inspection.json``, next to the document's record."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_text
from fraudcheck.inspect.models import Inspection

INSPECTION_NAME = "inspection.json"


def inspection_path(ingest: IngestSettings, doc_id: str) -> Path:
    return ingest.cache_path / doc_id / INSPECTION_NAME


def load_inspection(ingest: IngestSettings, doc_id: str) -> Inspection | None:
    """The saved inspection of a document, or ``None`` when it was never inspected."""
    try:
        return Inspection.model_validate_json(inspection_path(ingest, doc_id).read_bytes())
    except (OSError, ValueError, ValidationError):
        return None


def save_inspection(ingest: IngestSettings, inspection: Inspection) -> Path:
    path = inspection_path(ingest, inspection.id)
    atomic_write_text(path, inspection.model_dump_json(indent=1))
    return path
