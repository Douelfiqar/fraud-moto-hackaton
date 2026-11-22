"""Step 1 of the pipeline, Ingest: every document file becomes the same record.

Typical use from a later step::

    from fraudcheck.ingest import load_index, load_record, load_settings

    settings = load_settings()
    for entry in load_index(settings).documents:
        record = load_record(settings, entry.id)
"""

from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.models import (
    CONTEXT_CODES,
    FAMILIES,
    INGEST_VERSION,
    Code,
    DocumentRecord,
    IndexEntry,
    IngestIndex,
    Notice,
    Page,
    TextLayer,
    TextSpan,
)
from fraudcheck.ingest.pipeline import (
    IngestSetupError,
    RunResult,
    Summary,
    load_index,
    load_record,
    load_text_layer,
    run_ingest,
    summarise,
)
from fraudcheck.ingest.settings import IngestSettings, load_settings
from fraudcheck.ingest.storage import CacheWriteError

__all__ = [
    "CONTEXT_CODES",
    "FAMILIES",
    "INGEST_VERSION",
    "CacheWriteError",
    "Code",
    "ConfigError",
    "DocumentRecord",
    "IndexEntry",
    "IngestIndex",
    "IngestSettings",
    "IngestSetupError",
    "Notice",
    "Page",
    "RunResult",
    "Summary",
    "TextLayer",
    "TextSpan",
    "load_index",
    "load_record",
    "load_settings",
    "load_text_layer",
    "run_ingest",
    "summarise",
]
