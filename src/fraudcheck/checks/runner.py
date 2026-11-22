"""Run every registered check on every document, one document at a time, and write the check table.

For each document, the runner gathers that document's own record, text layer, reading
and inspection into a ``DocumentContext``, then runs every check on it. Nothing from
another document is ever put in the context: each document is judged on its own.

Outputs, under the cache folder:

- ``<id>/checks.json``: every check's result for that document, with its evidence;
- ``checks/results.json``: the same for all documents;
- ``checks/table.csv``: one row per document, one column per check (``;``-separated,
  opens directly in a French Excel).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from fraudcheck.checks.base import CheckResult, DocumentContext, registered
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.ingest.models import DocumentRecord, TextLayer
from fraudcheck.ingest.pipeline import load_index, load_record
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_text, read_text_layer
from fraudcheck.read.store import load_reading

log = logging.getLogger(__name__)

CHECKS_NAME = "checks.json"
FOLDER = "checks"
RESULTS_NAME = "results.json"
TABLE_NAME = "table.csv"

Results = dict[str, dict[str, CheckResult]]


class ChecksSetupError(RuntimeError):
    """The checks cannot run: Ingest has not run yet, or an unknown check was asked for."""


@dataclass
class CheckRun:
    results: Results
    checks: list[str]
    records: dict[str, DocumentRecord]
    table_path: Path
    results_path: Path
    seconds: float


def _own_text_layer(ingest: IngestSettings, record: DocumentRecord) -> TextLayer | None:
    if not record.text:
        return None
    try:
        return read_text_layer(ingest.resolve(record.text))
    except (OSError, ValueError):
        return None


def document_context(ingest: IngestSettings, settings: CheckSettings, record: DocumentRecord) -> DocumentContext:
    """Everything one document brings to the checks: its own files, nothing from another document."""
    from fraudcheck.inspect.store import load_inspection  # stage "inspect" may not have run

    return DocumentContext(
        record=record,
        reading=load_reading(ingest, record.id),
        text_layer=_own_text_layer(ingest, record),
        settings=settings,
        inspection=load_inspection(ingest, record.id),
    )


def _records(ingest: IngestSettings) -> list[DocumentRecord]:
    try:
        index = load_index(ingest)
    except FileNotFoundError as exc:
        raise ChecksSetupError("no index in the cache: run 'python -m fraudcheck ingest' first") from exc
    return [record for record in (load_record(ingest, entry.id) for entry in index.documents) if record is not None]


def _failed(check, error: Exception) -> CheckResult:
    return CheckResult(
        check=check.name,
        outcome="cannot_tell",
        evidence="Le contrôle n'a pas pu s'exécuter sur ce document (erreur interne).",
        source=check.source,
        details={"error": type(error).__name__},
    )


def check_document(context: DocumentContext, checks: dict) -> dict[str, CheckResult]:
    """Run the given checks on one document; a check that crashes gives ``cannot_tell`` for it only."""
    answers: dict[str, CheckResult] = {}
    for name, check in checks.items():
        try:
            if check.applies(context):
                answers[name] = check.run(context)
        except Exception as exc:  # isolate the document and the check
            log.warning("check %s failed on %s (%s)", name, context.id, type(exc).__name__, exc_info=True)
            answers[name] = _failed(check, exc)
    return answers


def run_checks(
    ingest: IngestSettings,
    settings: CheckSettings,
    *,
    only: Sequence[str] = (),
    progress: Callable[[int, int, str], None] | None = None,
) -> CheckRun:
    """Run the checks (all, or those named in ``only``) and save the results and the table."""
    started = time.perf_counter()
    checks = registered()
    unknown = sorted(set(only) - set(checks))
    if unknown:
        raise ChecksSetupError(f"unknown check(s): {', '.join(unknown)}; known: {', '.join(checks)}")
    chosen = {name: check for name, check in checks.items() if not only or name in only}
    records = _records(ingest)
    results: Results = {}
    for position, record in enumerate(records, start=1):
        if progress:
            progress(position, len(records), record.id)
        answers = _previous(ingest, record.id) if only else {}
        for name in chosen:
            answers.pop(name, None)
        answers.update(check_document(document_context(ingest, settings, record), chosen))
        results[record.id] = answers
    columns = sorted(checks)
    by_id = {record.id: record for record in records}
    table_path, results_path = save_results(ingest, results, by_id, columns)
    return CheckRun(results, columns, by_id, table_path, results_path, time.perf_counter() - started)


def _previous(ingest: IngestSettings, doc_id: str) -> dict[str, CheckResult]:
    try:
        data = json.loads((ingest.cache_path / doc_id / CHECKS_NAME).read_text(encoding="utf-8"))
        return {name: CheckResult.model_validate(value) for name, value in data.items() if name in registered()}
    except (OSError, ValueError, ValidationError, AttributeError):
        return {}


def save_results(
    ingest: IngestSettings, results: Results, records: dict[str, DocumentRecord], columns: Sequence[str]
) -> tuple[Path, Path]:
    for doc_id, answers in results.items():
        payload = {name: answer.model_dump(mode="json") for name, answer in sorted(answers.items())}
        atomic_write_text(ingest.cache_path / doc_id / CHECKS_NAME, json.dumps(payload, indent=1, ensure_ascii=False))
    folder = ingest.cache_path / FOLDER
    everything = {
        doc_id: {name: answer.model_dump(mode="json") for name, answer in sorted(answers.items())}
        for doc_id, answers in sorted(results.items())
    }
    results_path = folder / RESULTS_NAME
    atomic_write_text(results_path, json.dumps(everything, indent=1, ensure_ascii=False))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["id", "family", "kind", "nature", *columns])
    for doc_id in sorted(results):
        record = records.get(doc_id)
        answers = results[doc_id]
        writer.writerow(
            [
                doc_id,
                record.family if record else "",
                record.kind if record else "",
                record.nature if record else "",
                *(answers[name].cell if name in answers else "" for name in columns),
            ]
        )
    table_path = folder / TABLE_NAME
    atomic_write_text(table_path, "﻿" + buffer.getvalue())  # the mark lets Excel read the accents
    return table_path, results_path


def load_results(ingest: IngestSettings) -> Results:
    """The results saved by the last run of the checks (empty when they never ran)."""
    try:
        data = json.loads((ingest.cache_path / FOLDER / RESULTS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Results = {}
    for doc_id, answers in data.items():
        try:
            out[doc_id] = {name: CheckResult.model_validate(value) for name, value in answers.items()}
        except ValidationError:
            continue
    return out
