"""Step 1, Ingest: turn every document file into the same record, so later steps never deal with formats.

For each file: give it an id, look up its nature, take its fingerprint, then either
reuse the cached record (same fingerprint, same settings) or process it: pictures
are used as they are or get a lighter working copy, PDF pages are drawn into
pictures and the text of text PDFs is saved with its fonts. One index lists all
records. Ingest makes no Azure call, reads nothing for meaning and judges nothing.
One bad file never stops the run: its record carries the error instead.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fraudcheck.ingest.barcodes import read_barcodes
from fraudcheck.ingest.catalogue import Catalogue, FamilyRule, family_for, load_catalogue, load_family_rules
from fraudcheck.ingest.discover import DUPLICATE_MARK, Candidate, Discovery, assign_ids, discover, natural_key, same_place
from fraudcheck.ingest.models import (
    CONTEXT_CODES,
    INGEST_VERSION,
    Code,
    DocumentRecord,
    IgnoredFile,
    IndexEntry,
    IngestIndex,
    Notice,
    TextLayer,
)
from fraudcheck.ingest.pdfs import PDF_EXTENSIONS, looks_like_pdf, process_pdf
from fraudcheck.ingest.pictures import PICTURE_EXTENSIONS, identify_picture, process_picture
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import (
    INDEX_NAME,
    RECORD_NAME,
    TEXT_NAME,
    CacheWriteError,
    atomic_write_text,
    read_index,
    read_record,
    read_text_layer,
    remove_stale_files,
    sha256_file,
)
from fraudcheck.ingest.words import read_words

log = logging.getLogger(__name__)

_HEAD_BYTES = 1024
_EXTENSIONS_OF = {
    "pdf": {".pdf"},
    "png": {".png"},
    "jpeg": {".jpg", ".jpeg", ".jpe", ".jfif"},
    "tiff": {".tif", ".tiff"},
    "webp": {".webp"},
    "gif": {".gif"},
    "bmp": {".bmp", ".dib"},
    "heif": {".heic", ".heif"},
    "jpeg2000": {".jp2", ".j2k", ".jpx", ".jpf"},
}

Progress = Callable[[int, int, str], None]


class IngestSetupError(RuntimeError):
    """The run cannot start: no documents folder, or none of the requested documents exists."""


@dataclass
class RunResult:
    records: list[DocumentRecord]
    reused: set[str]
    index: IngestIndex
    index_path: Path
    notices: list[Notice]
    seconds: float
    targeted: bool = False


@dataclass
class Summary:
    """Counts only: safe to print, holds no document content."""

    documents: int
    processed: int
    reused: int
    kinds: Counter[str]
    families: Counter[str]
    pages: int
    sources: Counter[str]
    warnings: Counter[str]
    documents_with_warnings: int
    errors: Counter[str]
    ignored: int
    catalogue_rows_without_file: int
    seconds: float
    targeted: bool
    index_path: Path
    notices: list[Notice] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def run_ingest(
    settings: IngestSettings,
    targets: Sequence[str] = (),
    *,
    force: bool = False,
    progress: Progress | None = None,
) -> RunResult:
    """Ingest the whole documents folder, or only ``targets`` (ids such as DOC-042, or file paths).

    Raises ``IngestSetupError`` or ``catalogue.ConfigError`` when the run cannot start, and
    ``storage.CacheWriteError`` when the cache cannot be written. Problems with single
    documents never raise: they end up in their records.
    """
    started = time.perf_counter()
    rules = load_family_rules(settings.families_file)
    folder = settings.documents_path
    if not folder.is_dir() and not targets:
        raise IngestSetupError(f"documents folder not found: {folder}")
    catalogue = load_catalogue(settings.catalogue_file, settings.catalogue_sheet)
    discovery = (
        discover(folder, exclude=[settings.cache_path], name_to_id=catalogue.ids_by_file())
        if folder.is_dir()
        else Discovery()
    )
    notices = list(catalogue.notices)
    if targets:
        chosen, outside, missing = _select(discovery, targets, settings, catalogue)
        notices += [Notice(code=Code.TARGET_NOT_FOUND, message=f"not found: {target}") for target in missing]
        if not chosen and not outside:
            raise IngestSetupError("none of the requested documents was found: " + ", ".join(missing))
    else:
        chosen, outside = list(discovery.candidates), []

    work = chosen + outside
    records: list[DocumentRecord] = []
    reused: set[str] = set()
    for position, candidate in enumerate(work, start=1):
        if progress:
            progress(position, len(work), candidate.id)
        record, was_reused = ingest_safely(candidate, settings, catalogue, rules, force=force)
        records.append(record)
        if was_reused:
            reused.add(record.id)
        log.debug("%s: kind=%s pages=%d reused=%s", record.id, record.kind, len(record.pages), was_reused)
    for candidate in outside:
        notices.append(
            Notice(
                code=Code.OUTSIDE_DOCUMENTS_FOLDER,
                message=f"{candidate.id} is outside the documents folder: it has a record but is not in the index",
            )
        )

    in_folder = {candidate.id for candidate in chosen}
    fresh = {record.id: record for record in records if record.id in in_folder}
    index = build_index(settings, discovery, fresh, catalogue, notices)
    index_path = settings.cache_path / INDEX_NAME
    atomic_write_text(index_path, index.model_dump_json(indent=1))
    return RunResult(
        records=records,
        reused=reused,
        index=index,
        index_path=index_path,
        notices=notices,
        seconds=time.perf_counter() - started,
        targeted=bool(targets),
    )


def _select(
    discovery: Discovery, targets: Sequence[str], settings: IngestSettings, catalogue: Catalogue
) -> tuple[list[Candidate], list[Candidate], list[str]]:
    """Match each target to the folder's documents by id or path; other existing files come 'outside'."""
    by_id: dict[str, list[Candidate]] = {}
    for candidate in discovery.candidates:
        for key in {candidate.id.casefold(), candidate.base_id.casefold()}:
            by_id.setdefault(key, []).append(candidate)
    by_place = {same_place(candidate.path): candidate for candidate in discovery.candidates}
    taken = {candidate.id.casefold() for candidate in discovery.candidates}
    chosen: list[Candidate] = []
    outside: list[Candidate] = []
    missing: list[str] = []
    for target in targets:
        matches = by_id.get(target.strip().casefold())
        if matches:
            chosen.extend(candidate for candidate in matches if candidate not in chosen)
            continue
        path = Path(target).expanduser()
        places = [path] if path.is_absolute() else [Path.cwd() / path, settings.root / path]
        found = next((place for place in places if place.is_file()), None)
        if found is None:
            missing.append(target)
            continue
        if same_place(found) in by_place:
            candidate = by_place[same_place(found)]
            if candidate not in chosen:
                chosen.append(candidate)
            continue
        [candidate] = assign_ids([found], catalogue.ids_by_file())
        if candidate.id.casefold() in taken:
            rank = 2
            while f"{candidate.base_id}{DUPLICATE_MARK}{rank}".casefold() in taken:
                rank += 1
            new_id = f"{candidate.base_id}{DUPLICATE_MARK}{rank}"
            note = Notice(
                code=Code.DUPLICATE_ID,
                message=f"a document of the folder already has the id {candidate.base_id}; "
                f"this file is stored as {new_id}",
            )
            candidate = Candidate(found, new_id, candidate.base_id, (*candidate.warnings, note))
        taken.add(candidate.id.casefold())
        outside.append(candidate)
    return chosen, outside, missing


def ingest_safely(
    candidate: Candidate,
    settings: IngestSettings,
    catalogue: Catalogue,
    rules: Sequence[FamilyRule],
    *,
    force: bool = False,
) -> tuple[DocumentRecord, bool]:
    """``ingest_document``, with any unexpected failure turned into a record that carries the error.

    Only ``CacheWriteError`` goes through: when the cache cannot be written, the run must stop.
    """
    try:
        return ingest_document(candidate, settings, catalogue, rules, force=force)
    except CacheWriteError:
        raise
    except Exception as exc:  # a bug or a system failure on one file must not stop the run
        log.debug("unexpected failure on %s", candidate.id, exc_info=True)
        record = DocumentRecord(
            id=candidate.id,
            source_file=settings.display(candidate.path),
            warnings=list(candidate.warnings),
            error=Notice(code=Code.INTERNAL_ERROR, message=f"unexpected failure ({type(exc).__name__}: {exc})"[:300]),
            output_key=settings.output_key(),
            ingested_at=_now(),
        )
        _write_record(record, settings.cache_path / candidate.id)
        return record, False


def ingest_document(
    candidate: Candidate,
    settings: IngestSettings,
    catalogue: Catalogue,
    rules: Sequence[FamilyRule],
    *,
    force: bool = False,
) -> tuple[DocumentRecord, bool]:
    """Make (or reuse) the record of one document. Returns the record and whether it came from the cache."""
    out_dir = settings.cache_path / candidate.id
    nature, family, context = describe_context(candidate, catalogue, rules)
    record = DocumentRecord(
        id=candidate.id,
        source_file=settings.display(candidate.path),
        nature=nature,
        family=family,
        warnings=context,
        output_key=settings.output_key(),
    )
    try:
        size = candidate.path.stat().st_size
        fingerprint = sha256_file(candidate.path)
        with candidate.path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError as exc:
        return _failed(record, out_dir, Code.UNREADABLE_FILE, f"the file could not be read ({_reason(exc)})"), False
    record = record.model_copy(update={"size_bytes": size, "fingerprint": fingerprint})

    if not force:
        previous = read_record(out_dir / RECORD_NAME)
        if previous is not None and _reusable(previous, record, settings):
            kept = [notice for notice in previous.warnings if notice.code not in CONTEXT_CODES]
            refreshed = previous.model_copy(
                update={
                    "source_file": record.source_file,
                    "nature": nature,
                    "family": family,
                    "warnings": context + kept,
                }
            )
            if refreshed != previous:
                _write_record(refreshed, out_dir)
            return refreshed, True

    if size == 0:
        return _failed(record, out_dir, Code.EMPTY_FILE, "the file is empty (0 bytes)"), False
    extension = candidate.path.suffix.lower()
    file_format = "pdf" if looks_like_pdf(head) else identify_picture(candidate.path)
    if file_format is None:
        if extension in PDF_EXTENSIONS | PICTURE_EXTENSIONS:
            message = f"the content is not a readable {extension[1:].upper()} file"
            return _failed(record, out_dir, Code.CORRUPT_FILE, message), False
        message = f"format not supported (extension '{extension or 'none'}')"
        return _failed(record, out_dir, Code.UNSUPPORTED_FORMAT, message), False

    warnings = list(context)
    if extension not in _EXTENSIONS_OF.get(file_format, {f".{file_format}"}):
        warnings.append(
            Notice(
                code=Code.EXTENSION_MISMATCH,
                message=f"the extension is '{extension or 'none'}' but the content is {file_format.upper()}",
            )
        )
    update: dict[str, object] = {"file_format": file_format}
    if file_format == "pdf":
        try:
            data = candidate.path.read_bytes()
        except OSError as exc:
            return _failed(record, out_dir, Code.UNREADABLE_FILE, f"the file could not be read ({_reason(exc)})"), False
        update["fingerprint"] = hashlib.sha256(data).hexdigest()  # the exact bytes processed
        outcome = process_pdf(data, candidate.id, out_dir, settings, settings.display)
        update.update(kind=outcome.kind, upright=outcome.upright, text=outcome.text, fonts=outcome.fonts)
    else:
        outcome = process_picture(candidate.path, file_format, size, out_dir, settings, settings.display)
        update["kind"] = "picture" if outcome.pages else None
    pages = outcome.pages
    if settings.local_barcodes:
        pages = [page.model_copy(update={"barcodes": read_barcodes(settings.resolve(page.picture))}) for page in pages]
    if settings.local_words and family in settings.local_words_families:
        pages = [page.model_copy(update={"printed_words": read_words(settings.resolve(page.picture))}) for page in pages]
    update.update(pages=pages, warnings=warnings + outcome.warnings, error=outcome.error, ingested_at=_now())
    record = record.model_copy(update=update)

    keep = {Path(page.picture).name for page in record.pages if page.source != "original"}
    if record.text:
        keep.add(TEXT_NAME)
    remove_stale_files(out_dir, keep)
    _write_record(record, out_dir)
    return record, False


def describe_context(
    candidate: Candidate, catalogue: Catalogue, rules: Sequence[FamilyRule]
) -> tuple[str | None, str | None, list[Notice]]:
    """Nature and family from the spreadsheet, and the warnings about the document's context."""
    warnings = list(candidate.warnings)
    row = catalogue.lookup(candidate.base_id)
    if row is None:
        reason = "it has no row in the spreadsheet" if catalogue.available else "the spreadsheet is not available"
        warnings.append(Notice(code=Code.NO_CATALOGUE_ROW, message=f"nature unknown: {reason}"))
        return None, None, warnings
    if row.file and row.file.casefold() != candidate.path.name.casefold():
        warnings.append(
            Notice(
                code=Code.CATALOGUE_FILE_MISMATCH,
                message=f"the spreadsheet names the file '{row.file}' for {row.id}",
            )
        )
    if row.nature is None:
        warnings.append(Notice(code=Code.NATURE_MISSING, message=f"row {row.line} of the spreadsheet gives no nature"))
        return None, None, warnings
    family = family_for(row.nature, rules)
    if family is None:
        warnings.append(
            Notice(code=Code.NATURE_UNMAPPED, message="the nature matches no rule of the family rules file")
        )
    return row.nature, family, warnings


def _reusable(previous: DocumentRecord, current: DocumentRecord, settings: IngestSettings) -> bool:
    """A cached record is reused when the file and settings are unchanged and its outputs still exist."""
    if previous.error is not None or not previous.pages:
        return False
    if (
        previous.fingerprint != current.fingerprint
        or previous.output_key != current.output_key
        or previous.ingest_version != INGEST_VERSION
    ):
        return False
    for page in previous.pages:
        if page.source == "original":
            if page.picture != current.source_file:
                return False
        elif not settings.resolve(page.picture).is_file():
            return False
    return not previous.text or settings.resolve(previous.text).is_file()


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _failed(record: DocumentRecord, out_dir: Path, code: Code, message: str) -> DocumentRecord:
    failed = record.model_copy(update={"error": Notice(code=code, message=message), "ingested_at": _now()})
    remove_stale_files(out_dir, keep=set())
    _write_record(failed, out_dir)
    return failed


def _write_record(record: DocumentRecord, out_dir: Path) -> None:
    atomic_write_text(out_dir / RECORD_NAME, record.model_dump_json(indent=1))


def build_index(
    settings: IngestSettings,
    discovery: Discovery,
    fresh: dict[str, DocumentRecord],
    catalogue: Catalogue,
    notices: list[Notice],
) -> IngestIndex:
    """List the records of every document in the folder: this run's, or the cached ones for the others."""
    entries = []
    for candidate in discovery.candidates:
        record_path = settings.cache_path / candidate.id / RECORD_NAME
        record = fresh.get(candidate.id) or read_record(record_path)
        if record is None or record.source_file != settings.display(candidate.path):
            continue  # not ingested yet
        entries.append(IndexEntry.from_record(record, settings.display(record_path)))
    entries.sort(key=lambda entry: natural_key(entry.id))
    present = {candidate.base_id.casefold() for candidate in discovery.candidates}
    return IngestIndex(
        updated_at=_now(),
        documents_dir=settings.display(settings.documents_path),
        catalogue=settings.display(catalogue.path) if catalogue.available else None,
        documents=entries,
        catalogue_rows_without_file=sorted(catalogue.rows_without_file(present), key=natural_key),
        ignored=[IgnoredFile(path=settings.display(path), reason=reason) for path, reason in discovery.ignored],
        notices=notices,
    )


def summarise(result: RunResult) -> Summary:
    """Counts by kind, family, page source, warning and error, for printing."""
    records = result.records
    warnings: Counter[str] = Counter()
    for record in records:
        warnings.update({notice.code.value for notice in record.warnings})
    return Summary(
        documents=len(records),
        processed=len(records) - len(result.reused),
        reused=len(result.reused),
        kinds=Counter(record.kind or "unknown" for record in records),
        families=Counter(record.family or "unknown" for record in records),
        pages=sum(len(record.pages) for record in records),
        sources=Counter(page.source for record in records for page in record.pages),
        warnings=warnings,
        documents_with_warnings=sum(1 for record in records if record.warnings),
        errors=Counter(record.error.code.value for record in records if record.error),
        ignored=len(result.index.ignored),
        catalogue_rows_without_file=len(result.index.catalogue_rows_without_file),
        seconds=result.seconds,
        targeted=result.targeted,
        index_path=result.index_path,
        notices=result.notices,
    )


# Reading results back, for later steps.


def load_index(settings: IngestSettings) -> IngestIndex:
    """The index written by the last run. Raises ``FileNotFoundError`` when Ingest never ran."""
    return read_index(settings.cache_path / INDEX_NAME)


def load_record(settings: IngestSettings, doc_id: str) -> DocumentRecord | None:
    return read_record(settings.cache_path / doc_id / RECORD_NAME)


def load_text_layer(settings: IngestSettings, record: DocumentRecord) -> TextLayer | None:
    """The text layer of a ``pdf_text`` record, ``None`` for other kinds."""
    return read_text_layer(settings.resolve(record.text)) if record.text else None
