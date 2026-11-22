"""The documents of one session, and what a claims handler does with them.

A document is added (its file kept in its own temporary folder), given a nature,
analysed, then decided by the handler. One worker thread analyses the documents one at
a time: the local text reader is heavy, and a single queue keeps the stages in order.

The handler's decision goes through the pipeline's own review file and ``decide``, in
the document's own folder: it replaces the tool's decision exactly as it does in a
batch run. Nothing is ever written in the repository's ``data/`` or ``cache/``; the
team's saved Azure answers and its index are only read.
"""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import hashlib
import importlib.util
import io
import json
import logging
import os
import queue
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fraudcheck.decision.decide import ReviewDecision, decide
from fraudcheck.decision.review import remove_review_decision, save_review_decision
from fraudcheck.demo.analysis import (
    STAGES,
    Analyst,
    SavedAnswers,
    StageReport,
    decision_config,
    decision_payload,
    result_payload,
)
from fraudcheck.demo.doc_choices import check_kinds, kind_counts, type_choices
from fraudcheck.demo.workspace import Workspace, document_id, preview_jpeg, sniff_format, turned_jpeg
from fraudcheck.ingest.settings import load_settings
from fraudcheck.ingest.storage import INDEX_NAME, read_index
from fraudcheck.inspect.store import INSPECTION_NAME
from fraudcheck.read.gateway import ANSWERS_FOLDER, Gateway, missing_configuration
from fraudcheck.read.settings import ReadSettings, load_read_settings
from fraudcheck.read.store import READING_NAME

log = logging.getLogger(__name__)

WORK_PREFIX = "fraudcheck-demo-"
#: A running session marks its folder this often; a folder not marked for much longer was left by a session
#: that was killed, and the next session removes it. The folders hold copies of real people's documents.
HEARTBEAT_S = 60
STALE_AFTER_S = 15 * 60
MAX_REASON_CHARS = 600
_FORMULA_STARTS = ("=", "+", "-", "@")


class DeskError(ValueError):
    """Something the handler asked cannot be done; the message is shown on the page, in French."""


class UnknownDocument(KeyError):
    """No document of the session has this key."""


@dataclass(frozen=True)
class DeskSettings:
    """Where the desk works and what it may read."""

    work_dir: Path  # a temporary folder outside the repository: uploads and what the stages write
    saved_answers: tuple[Path, ...] = ()  # folders of saved Azure answers, only read
    known_index: Path | None = None  # the team's ingest index, only read: gives a known file its nature
    live: bool = False  # call Azure for a document that has no saved answer

    @classmethod
    def from_repository(cls, *, live: bool = False) -> DeskSettings:
        """The settings of a real session: a new temporary folder, the team's saved answers and index."""
        ingest = load_settings()
        _remove_stale_folders(Path(tempfile.gettempdir()))
        work_dir = Path(tempfile.mkdtemp(prefix=WORK_PREFIX)).resolve()
        if work_dir.is_relative_to(ingest.root):
            shutil.rmtree(work_dir, ignore_errors=True)
            raise DeskError(f"the temporary folder is inside the repository ({work_dir}): set TMP to a folder outside it")
        cache = ingest.cache_path
        return cls(work_dir=work_dir, saved_answers=(cache / ANSWERS_FOLDER,), known_index=cache / INDEX_NAME, live=live)


def _remove_stale_folders(temporary: Path) -> None:
    limit = time.time() - STALE_AFTER_S
    for folder in temporary.glob(WORK_PREFIX + "*"):
        try:
            if folder.is_dir() and folder.stat().st_mtime < limit:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


@dataclass(frozen=True)
class KnownDocument:
    """A file the team's own index already lists: its id, its nature, and the deployments of its saved answers."""

    id: str
    nature: str | None
    models: dict[str, str | None]
    results: dict[str, Path]  # the reading and the inspection the batch run saved for it


def find_known(index_path: Path | None, fingerprint: str) -> KnownDocument | None:
    """The document of the team's set that is this very file, by fingerprint. ``None`` when there is none."""
    if index_path is None:
        return None
    try:
        entries = read_index(index_path).documents
    except (OSError, ValueError):
        return None
    entry = next((entry for entry in entries if entry.fingerprint == fingerprint), None)
    if entry is None:
        return None
    folder = index_path.parent / entry.id
    results = {"read": folder / READING_NAME, "inspect": folder / INSPECTION_NAME}
    models = {stage: _saved_model(path, fingerprint) for stage, path in results.items()}
    return KnownDocument(entry.id, entry.nature, models, results)


def _saved_model(path: Path, fingerprint: str) -> str | None:
    """The deployment that produced a saved reading or inspection of this file. Only that name is read."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    model = saved.get("model") if isinstance(saved, dict) and saved.get("fingerprint") == fingerprint else None
    return model if isinstance(model, str) else None


def count_pages(data: bytes, file_format: str) -> int:
    if file_format != "pdf":
        return 1
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            return max(1, document.page_count)
    except Exception:  # a damaged PDF: the pipeline reports it, the viewer shows one empty page
        return 1


@dataclass
class Document:
    """One uploaded document and where it stands."""

    key: str  # the session's handle; never the file name
    name: str  # the file name as received, shown to the handler only
    doc_id: str
    file_format: str
    size: int
    fingerprint: str
    pages: int
    workspace: Workspace
    suggested_nature: str | None = None
    known: bool = False
    models: dict[str, str | None] = field(default_factory=dict)
    saved_results: dict[str, Path] = field(default_factory=dict)
    nature: str | None = None
    status: str = "uploaded"  # uploaded, queued, running, done or failed
    stages: list[StageReport] = field(default_factory=lambda: [StageReport(key) for key in STAGES])
    result: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    error: str | None = None
    added_at: datetime = field(default_factory=datetime.now)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    previews: dict[int, bytes | None] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "key": self.key, "name": self.name, "doc_id": self.doc_id, "format": self.file_format,
                "size": self.size, "pages": self.pages, "nature": self.nature,
                "suggested_nature": self.suggested_nature, "known": self.known, "status": self.status,
                "stages": [stage.as_dict() for stage in self.stages], "result": self.result,
                "review": self.review, "final": self.final, "error": self.error,
                "added_at": self.added_at.isoformat(timespec="seconds"),
            }


class Desk:
    """The session: its documents, the worker that analyses them, the handler's decisions."""

    def __init__(self, settings: DeskSettings, *, live_gateway: Callable[[ReadSettings], Gateway] | None = None) -> None:
        self.settings = settings
        self._live_gateway = live_gateway
        self._documents: dict[str, Document] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._kinds = check_kinds()  # loads every check module once, before any thread needs them
        self._answers = SavedAnswers(settings.work_dir / "answers", settings.saved_answers)
        self._closed = False
        settings.work_dir.mkdir(parents=True, exist_ok=True)
        self._worker = threading.Thread(target=self._work, name="fraudcheck-desk", daemon=True)
        self._worker.start()
        # Beside the worker, not before it: a document dropped at once is not kept waiting for the loading.
        threading.Thread(target=self._warm_up, name="fraudcheck-desk-warm-up", daemon=True).start()

    # What the page needs before any upload.

    def setup(self) -> dict[str, Any]:
        reading = load_read_settings()
        counts = kind_counts()
        return {
            "families": type_choices(reading.doc_types_folder),
            "checks": {"total": sum(counts.values()), "kinds": counts},
            "mode": {
                "live": self.settings.live and not missing_configuration(reading),
                "live_asked": self.settings.live,
                "saved_answers": any(folder.is_dir() for folder in self.settings.saved_answers),
                "words_reader": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
                "codes_reader": importlib.util.find_spec("zxingcpp") is not None,
            },
        }

    # Documents.

    def documents(self) -> list[Document]:
        with self._lock:
            return sorted(self._documents.values(), key=lambda document: document.added_at)

    def get(self, key: str) -> Document:
        with self._lock:
            document = self._documents.get(key)
        if document is None:
            raise UnknownDocument(key)
        return document

    def add(self, name: str, data: bytes) -> tuple[Document, bool]:
        """Take a file in. Returns the document and whether this very file was already in the session."""
        if not data:
            raise DeskError("Le fichier est vide.")
        file_format = sniff_format(data)
        if file_format is None:
            raise DeskError("Format non pris en charge : déposez un fichier PDF, PNG ou JPG.")
        fingerprint = hashlib.sha256(data).hexdigest()
        with self._lock:
            for document in self._documents.values():
                if document.fingerprint == fingerprint:
                    return document, True
        known = find_known(self.settings.known_index, fingerprint)
        key = secrets.token_hex(6)
        workspace = Workspace(self.settings.work_dir / key, document_id(known.id if known else name), file_format)
        workspace.write_document(data)
        document = Document(
            key=key, name=name or workspace.file.name, doc_id=workspace.doc_id, file_format=file_format, size=len(data),
            fingerprint=fingerprint, pages=count_pages(data, file_format), workspace=workspace,
            suggested_nature=known.nature if known else None, known=known is not None,
            models=known.models if known else {}, saved_results=known.results if known else {},
        )
        document.previews[1] = preview_jpeg(data, file_format, 1)
        with self._lock:
            self._documents[key] = document
        return document, False

    def page(self, key: str, number: int, turn: int = 0) -> bytes | None:
        """A light picture of one page, turned ``turn`` degrees clockwise, for the viewer.

        ``None`` when the page does not exist or cannot be drawn.
        """
        document = self.get(key)
        if not 1 <= number <= document.pages:
            return None
        with document.lock:
            if number not in document.previews:
                try:
                    data = document.workspace.file.read_bytes()
                except OSError:
                    return None
                document.previews[number] = preview_jpeg(data, document.file_format, number)
            picture = document.previews[number]
        return turned_jpeg(picture, turn) if picture and turn % 360 else picture

    def start(self, key: str, nature: str) -> Document:
        """Give the document its nature and queue its analysis (again, when it was already analysed)."""
        document = self.get(key)
        offered = {choice["nature"] for family in self.setup()["families"] for choice in family["natures"]}
        if nature not in offered:
            raise DeskError("Choisissez le type du document dans la liste.")
        with document.lock:
            if document.status in ("queued", "running"):
                return document
            shutil.rmtree(document.workspace.folder / "cache", ignore_errors=True)
            document.workspace.review_file.unlink(missing_ok=True)
            document.workspace.write_catalogue(nature, load_settings().catalogue_sheet)
            document.nature, document.status = nature, "queued"
            document.stages = [StageReport(stage) for stage in STAGES]
            document.result = document.review = document.final = document.error = None
        self._queue.put(key)
        return document

    def remove(self, key: str) -> None:
        document = self.get(key)
        with document.lock:
            if document.status in ("queued", "running"):
                raise DeskError("Ce document est en cours de contrôle : attendez la fin pour le retirer.")
            with self._lock:
                self._documents.pop(key, None)
            document.workspace.remove()

    # The handler's decision.

    def decide(self, key: str, *, fake: bool, reason: str = "", generated: bool = False) -> Document:
        """Record the handler's decision; it replaces the tool's, through the pipeline's own review file."""
        document = self.get(key)
        reason = " ".join(reason.split())[:MAX_REASON_CHARS]
        with document.lock:
            if document.status != "done" or document.result is None:
                raise DeskError("Le contrôle de ce document n'est pas terminé.")
            if fake and not reason:
                raise DeskError("Un document jugé faux demande un motif.")
            settings = document.workspace.settings()
            save_review_decision(
                settings.decision.review_decisions_file,
                ReviewDecision(id=document.doc_id, fake=fake, reason=reason, generated=generated and fake),
            )
            final = decide(settings.ingest, settings.decision).decisions[0]
            document.final = decision_payload(final)
            document.review = {
                "fake": fake, "reason": reason, "generated": generated and fake,
                "agrees": fake == document.result["decision"]["flagged"],
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        return document

    def reopen(self, key: str) -> Document:
        """Take the handler's decision back: the tool's decision applies again until they decide."""
        document = self.get(key)
        with document.lock:
            if document.review is None:
                return document
            remove_review_decision(document.workspace.review_file, document.doc_id)
            document.review = document.final = None
        return document

    def journal_csv(self) -> str:
        """The handler's decisions of the session, one line per document, readable in a French Excel."""
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
        writer.writerow(["fichier", "identifiant", "type", "verdict_outil", "confiance_outil", "decision_personne",
                         "accord", "motif", "genere_ia", "date"])
        for document in self.documents():
            with document.lock:
                if document.review is None or document.result is None:
                    continue
                tool = document.result["decision"]
                verdict = "signalé" if tool["flagged"] else ("illisible" if tool["line"] == "unreadable" else "non signalé")
                confidence = "" if tool["confidence"] is None else f"{tool['confidence']:.2f}".replace(".", ",")
                review = document.review
                writer.writerow([
                    _cell(document.name), document.doc_id, document.nature or "", verdict, confidence,
                    "faux" if review["fake"] else "authentique", "confirme" if review["agrees"] else "corrige",
                    _cell(review["reason"]), "oui" if review["generated"] else "non", review["at"],
                ])
        return "﻿" + buffer.getvalue()  # the mark lets Excel read the accents

    # The worker.

    def _work(self) -> None:
        while True:
            try:
                key = self._queue.get(timeout=HEARTBEAT_S)
            except queue.Empty:
                with contextlib.suppress(OSError):
                    os.utime(self.settings.work_dir)  # "this session is alive": see STALE_AFTER_S
                continue
            if key is None:
                return
            with self._lock:
                document = self._documents.get(key)
            if document is not None and document.status == "queued":
                self._analyse(document)

    def _warm_up(self) -> None:
        """Load what is slow on first use before the first document, so that it is not the first user's wait:
        the local text reader (a few seconds) and the word lists of the wording check (about ten seconds)."""
        try:
            from fraudcheck.ingest.words import reader_installed

            reader_installed()
        except Exception:  # an optional reader that cannot start is the pipeline's to report, per document
            log.debug("the local text reader could not be prepared", exc_info=True)
        try:
            from fraudcheck.checks.laptop.official_wording import dictionary
            from fraudcheck.checks.settings import load_check_settings

            dictionary(tuple(load_check_settings().wording_languages))
        except Exception:  # optional as well: without it the check loads its word lists on its first document
            log.debug("the word lists could not be prepared", exc_info=True)

    def _analyse(self, document: Document) -> None:
        with document.lock:
            document.status = "running"
        try:
            settings = document.workspace.settings()
            # The batch run read this file as the nature of the spreadsheet: its saved result stands for no other.
            as_in_the_batch = document.nature == document.suggested_nature
            analyst = Analyst(
                settings, self._answers, live=self.settings.live, preferred_models=document.models,
                saved_results=document.saved_results if as_in_the_batch else {}, report=lambda stage: self._stage_reported(document, stage), live_gateway=self._live_gateway,
            )
            analysed = analyst.run()
            result = result_payload(analysed, decision_config(settings), self._kinds)
        except Exception as exc:  # one document that cannot be analysed never stops the desk
            log.warning("the analysis of one document failed (%s)", type(exc).__name__)
            log.debug("failure details", exc_info=True)
            with document.lock:
                document.status, document.error = "failed", type(exc).__name__
            return
        with document.lock:
            document.result, document.status = result, "done"

    def _stage_reported(self, document: Document, stage: StageReport) -> None:
        copy = dataclasses.replace(stage, facts=dict(stage.facts))
        with document.lock:
            document.stages = [copy if known.key == copy.key else known for known in document.stages]

    def close(self) -> None:
        """Stop the worker and delete every file of the session."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=5)
        shutil.rmtree(self.settings.work_dir, ignore_errors=True)


def _cell(text: str) -> str:
    """Text typed by a person, made safe for a spreadsheet: a leading ``=`` would be run as a formula."""
    return "'" + text if text.startswith(_FORMULA_STARTS) else text
