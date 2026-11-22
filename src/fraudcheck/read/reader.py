"""Step 2, Read: one Azure request per document turns its page pictures into named values.

The request carries every page picture of the document, plus the PDF's own text when
there is some. It states the nature from the spreadsheet and the fields of that
nature (``config/doc_types``); when the nature is unknown, the model first says what
the document is. When the answer says a page is sideways, that page is turned and
the document is read once more. Each reading is saved in ``cache/<id>/reading.json``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from fraudcheck.ingest.models import DocumentRecord
from fraudcheck.ingest.pipeline import load_index, load_record
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_bytes, read_text_layer
from fraudcheck.read.doc_types import DocType, doc_type_for, load_doc_types
from fraudcheck.read.gateway import (
    ANSWERS_FOLDER,
    DOCUMENT_REASONS,
    TOKEN_LOG,
    AnswerCache,
    AzureGateway,
    Gateway,
    GatewayAnswer,
    GatewaySetupError,
    ReadRequest,
    ReplayGateway,
    TokenLog,
    missing_configuration,
)
from fraudcheck.read.models import Reading, ReadingAnswer
from fraudcheck.read.prompt import Prompt, load_prompt, user_text
from fraudcheck.read.settings import ReadSettings
from fraudcheck.read.store import save_reading

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
UNSET_MODEL = "unset"


class ReadSetupError(RuntimeError):
    """The reading cannot start: Ingest never ran, or an asked document is unknown."""


@dataclass
class ReadRun:
    readings: list[Reading] = field(default_factory=list)
    live_calls: int = 0
    from_cache: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    replay: bool = False
    model: str | None = None
    not_read: list[str] = field(default_factory=list)  # nothing saved: this run could not ask (no answer saved, network)

    def cost(self, settings: ReadSettings) -> float | None:
        if settings.price_input_per_million is None or settings.price_output_per_million is None:
            return None
        return (self.input_tokens * settings.price_input_per_million + self.output_tokens * settings.price_output_per_million) / 1e6


def request_key(record: DocumentRecord, model: str, prompt_version: str, text: str, turns: dict[int, int], detail: str) -> str:
    """Key of the disk cache: the document's fingerprint, the prompt version, the model, and what was sent."""
    payload = [record.fingerprint, record.output_key, model, prompt_version, detail, sorted(turns.items()),
               hashlib.sha256(text.encode("utf-8")).hexdigest()]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()[:32]


def turned_picture(ingest: IngestSettings, record: DocumentRecord, number: int, degrees: int) -> Path:
    """A copy of page ``number`` turned ``degrees`` clockwise, saved next to the record."""
    page = record.pages[number - 1]
    target = ingest.cache_path / record.id / f"turned-{number}-{degrees}.png"
    if not target.is_file():
        with Image.open(ingest.resolve(page.picture)) as image:
            turned = image.convert("RGB").rotate(-degrees, expand=True)
        buffer = io.BytesIO()
        turned.save(buffer, format="PNG", compress_level=3)
        atomic_write_bytes(target, buffer.getvalue())
    return target


def picture_part(path: Path, detail: str) -> dict:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = MIME.get(path.suffix.lower(), "image/png")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": detail}}


def build_request(
    record: DocumentRecord,
    ingest: IngestSettings,
    settings: ReadSettings,
    prompt: Prompt,
    doc_type: DocType | None,
    types: Sequence[DocType],
    model: str,
    turns: dict[int, int],
) -> ReadRequest:
    layer = read_text_layer(ingest.resolve(record.text)) if record.text else None
    text = user_text(prompt, record.nature, doc_type, types, layer, len(record.pages), settings.max_text_chars)
    pictures = [
        turned_picture(ingest, record, page.number, turns[page.number]) if page.number in turns else ingest.resolve(page.picture)
        for page in record.pages
    ]
    content = [{"type": "text", "text": text}, *(picture_part(path, settings.image_detail) for path in pictures)]
    return ReadRequest(
        doc_id=record.id,
        key=request_key(record, model, prompt.version, text, turns, settings.image_detail),
        model=model,
        prompt_version=prompt.version,
        messages=[{"role": "system", "content": prompt.system}, {"role": "user", "content": content}],
    )


def in_doubt(answer: ReadingAnswer) -> bool:
    """True when the reader gave a value up at its main place while it copied the same value from another place.

    The reader then saw the value on the document but would not commit on one print of it. That is the place
    where a typed-over value hides, and a rule that compares the prints of a value has nothing left to compare:
    the document is worth one reading by the stronger deployment.
    """
    return any(item.state == "unreadable" and any(copy.strip() for copy in item.other_copies) for item in answer.fields)


def read_document(
    record: DocumentRecord,
    ingest: IngestSettings,
    settings: ReadSettings,
    prompt: Prompt,
    types: Sequence[DocType],
    gateway: Gateway,
    model: str,
) -> tuple[Reading, list[GatewayAnswer]]:
    """Read one document; turn sideways pages and read again when the answer says so; ask the stronger
    deployment once when the reading is in doubt (see ``in_doubt``)."""
    doc_type = doc_type_for(record.nature, types)
    base = {"id": record.id, "doc_type": doc_type.name if doc_type else None, "model": model,
            "prompt_version": prompt.version, "fingerprint": record.fingerprint}
    if not record.pages:
        return Reading(status="no_reading", reason="no page picture (ingest error)", read_at=datetime.now(UTC), **base), []
    answers = [gateway.ask(build_request(record, ingest, settings, prompt, doc_type, types, model, {}))]
    final, turns = answers[0], {}
    if final.status == "read" and final.answer and settings.reread_turned:
        wanted = {turn.page: int(turn.turn) for turn in final.answer.pages
                  if turn.turn != "0" and 1 <= turn.page <= len(record.pages)}
        if wanted:
            second = gateway.ask(build_request(record, ingest, settings, prompt, doc_type, types, model, wanted))
            answers.append(second)
            if second.status == "read":
                final, turns = second, wanted
    stronger = settings.strong_model
    if (settings.second_opinion and stronger and stronger != model and final.status == "read" and final.answer
            and in_doubt(final.answer)):
        opinion = gateway.ask(build_request(record, ingest, settings, prompt, doc_type, types, stronger, turns))
        answers.append(opinion)
        if opinion.status == "read" and opinion.answer:
            final, base["model"] = opinion, stronger
    reading = Reading(
        status=final.status, reason=final.reason, answer=final.answer, turns=turns, calls=len(answers),
        input_tokens=sum(a.input_tokens for a in answers), output_tokens=sum(a.output_tokens for a in answers),
        read_at=datetime.now(UTC), **base,
    )
    return reading, answers


def select_records(ingest: IngestSettings, only: Sequence[str], limit: int | None) -> list[DocumentRecord]:
    """The records of the index: all, ``only`` some (ids, or ids of split parts), or the first ``limit``."""
    try:
        entries = load_index(ingest).documents
    except FileNotFoundError as exc:
        raise ReadSetupError("no index in the cache: run 'python -m fraudcheck ingest' first") from exc
    if only:
        wanted = {doc_id.strip().casefold() for doc_id in only}
        chosen = [entry for entry in entries if entry.id.casefold() in wanted or entry.id.split("~")[0].casefold() in wanted]
        unknown = wanted - {entry.id.casefold() for entry in chosen} - {entry.id.split("~")[0].casefold() for entry in chosen}
        if unknown:
            raise ReadSetupError(f"unknown document(s): {', '.join(sorted(unknown))}")
        entries = chosen
    if limit is not None:
        entries = entries[: max(0, limit)]
    return [record for record in (load_record(ingest, entry.id) for entry in entries) if record is not None]


def run_read(
    ingest: IngestSettings,
    settings: ReadSettings,
    *,
    only: Sequence[str] = (),
    limit: int | None = None,
    replay: bool = False,
    force: bool = False,
    gateway: Gateway | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ReadRun:
    """Read the documents of the index (all, ``only`` some, or the first ``limit``) and save each reading.

    Raises ``ReadSetupError`` or ``GatewaySetupError`` when the run cannot start.
    """
    started = time.perf_counter()
    types = load_doc_types(settings.doc_types_folder)
    prompt = load_prompt(settings.prompt_file)
    records = select_records(ingest, only, limit)
    cache = AnswerCache(ingest.cache_path / ANSWERS_FOLDER)
    if gateway is None:
        if replay:
            gateway = ReplayGateway(cache)
        else:
            missing = missing_configuration(settings)
            if missing:
                raise GatewaySetupError("Azure is not configured; missing in .env: " + "; ".join(missing))
            gateway = AzureGateway(settings, cache, TokenLog(ingest.cache_path / TOKEN_LOG), force=force)
    model = settings.deployment or UNSET_MODEL
    run = ReadRun(replay=replay, model=model)
    for position, record in enumerate(records, start=1):
        if progress:
            progress(position, len(records), record.id)
        reading, answers = read_document(record, ingest, settings, prompt, types, gateway, model)
        run.live_calls += sum(answer.calls for answer in answers)
        run.from_cache += sum(answer.cached for answer in answers)
        run.input_tokens += sum(answer.input_tokens for answer in answers if not answer.cached)
        run.output_tokens += sum(answer.output_tokens for answer in answers if not answer.cached)
        if reading.status == "no_reading" and reading.reason not in DOCUMENT_REASONS and record.pages:
            # The run could not ask (answer not saved for replay, network failure): that says nothing
            # about the document, so nothing is saved and an earlier reading stays as it was.
            run.not_read.append(record.id)
            continue
        save_reading(ingest, reading)
        run.readings.append(reading)
    run.seconds = time.perf_counter() - started
    return run
