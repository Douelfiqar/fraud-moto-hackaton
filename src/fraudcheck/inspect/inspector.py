"""Step 3, Inspect: one Azure request per document, separate from the reading.

The request carries the document's pages turned upright (with the turns of its own
reading, when it was read), zoom tiles cut from those pages, the base form of its type
(``[base_form]`` in ``config/doc_types``) and, when the folder has some, reference
pictures of the same form filled for someone else (``data/templates/<type>/``). The
answer lists observations only; the checks of ``fraudcheck.checks.inspection`` turn
them into outcomes. Same gateway, same disk cache, same token log as the reading. Each
inspection is saved in ``cache/<id>/inspection.json``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from fraudcheck.ingest.models import DocumentRecord
from fraudcheck.ingest.pipeline import load_index
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.inspect.models import Inspection, InspectionAnswer
from fraudcheck.inspect.prompt import Prompt, load_prompt, user_text
from fraudcheck.inspect.settings import InspectSettings
from fraudcheck.inspect.store import save_inspection
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
from fraudcheck.read.models import Reading
from fraudcheck.read.reader import UNSET_MODEL, picture_part, select_records, turned_picture
from fraudcheck.read.settings import ReadSettings
from fraudcheck.read.store import load_reading

log = logging.getLogger(__name__)
REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_ROWS = {2: ("haut", "bas"), 3: ("haut", "milieu", "bas")}
_COLS = {2: ("gauche", "droite"), 3: ("gauche", "centre", "droite")}


@dataclass(frozen=True)
class Picture:
    caption: str
    data: bytes
    detail: str
    mime: str = "image/png"

    def part(self) -> dict:
        encoded = base64.b64encode(self.data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{self.mime};base64,{encoded}", "detail": self.detail}}


@dataclass
class InspectRun:
    inspections: list[Inspection] = field(default_factory=list)
    live_calls: int = 0
    from_cache: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    replay: bool = False
    model: str | None = None
    references_refused: int = 0  # reference pictures identical to a document of the set: never sent
    not_inspected: list[str] = field(default_factory=list)  # nothing saved: this run could not ask

    def cost(self, connection: ReadSettings) -> float | None:
        if connection.price_input_per_million is None or connection.price_output_per_million is None:
            return None
        return (self.input_tokens * connection.price_input_per_million
                + self.output_tokens * connection.price_output_per_million) / 1e6


def page_turns(record: DocumentRecord, reading: Reading | None) -> dict[int, int]:
    """Degrees to turn each page clockwise, from the document's own reading of the same file."""
    if reading is None or reading.fingerprint != record.fingerprint:
        return {}
    pages = len(record.pages)
    if reading.turns:
        return {int(number): int(degrees) for number, degrees in reading.turns.items()
                if 1 <= int(number) <= pages and int(degrees) % 360}
    if reading.answer is not None:
        return {turn.page: int(turn.turn) for turn in reading.answer.pages if turn.turn != "0" and 1 <= turn.page <= pages}
    return {}


def tile_boxes(width: int, height: int, rows: int, cols: int, overlap: float) -> Iterator[tuple[int, int, tuple[int, int, int, int]]]:
    """Boxes of a ``rows`` x ``cols`` grid covering the page, each sharing ``overlap`` of its size with its neighbour."""
    tile_w = width / (cols - (cols - 1) * overlap)
    tile_h = height / (rows - (rows - 1) * overlap)
    for row in range(rows):
        for col in range(cols):
            left, top = round(col * tile_w * (1 - overlap)), round(row * tile_h * (1 - overlap))
            yield row, col, (left, top, min(width, round(left + tile_w)), min(height, round(top + tile_h)))


def tile_caption(number: int, row: int, col: int, rows: int, cols: int) -> str:
    vertical = _ROWS.get(rows, ())[row] if rows in _ROWS else f"bande {row + 1} sur {rows}"
    horizontal = _COLS.get(cols, ())[col] if cols in _COLS else f"colonne {col + 1} sur {cols}"
    where = " ".join(part for part, count in ((vertical, rows), (horizontal, cols)) if count > 1)
    return f"Page {number}, zoom : partie {where}"


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=3)
    return buffer.getvalue()


def zoom_tiles(pages: Sequence[tuple[int, Path]], settings: InspectSettings) -> list[Picture]:
    """Zoom tiles of the pages, in page order, at most ``max_tiles`` for the document."""
    rows, cols = settings.tile_rows, settings.tile_cols
    if rows * cols < 2 or settings.max_tiles == 0:
        return []
    tiles: list[Picture] = []
    for number, path in pages:
        if len(tiles) + rows * cols > settings.max_tiles:
            break  # this page and the next ones are sent whole only
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        for row, col, box in tile_boxes(image.width, image.height, rows, cols, settings.tile_overlap):
            tiles.append(Picture(tile_caption(number, row, col, rows, cols), _png(image.crop(box)), settings.image_detail))
    return tiles


def reference_files(settings: InspectSettings, doc_type: DocType | None) -> list[Path]:
    """Reference pictures of the type: ``data/templates/<type>/*.png|jpg``, by name; none when the folder is missing."""
    if doc_type is None or settings.max_references == 0:
        return []
    folder = settings.templates_folder / doc_type.name
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in REFERENCE_SUFFIXES)


def reference_pictures(settings: InspectSettings, doc_type: DocType | None, refused: set[str]) -> tuple[list[tuple[str, Picture]], int]:
    """(digest, picture) of the references sent, and how many were refused as documents of the set."""
    chosen, refused_count = [], 0
    for path in reference_files(settings, doc_type):
        if len(chosen) >= settings.max_references:
            break
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in refused:
            refused_count += 1  # a document of the set is never a reference: each document is judged on its own
            continue
        try:
            with Image.open(io.BytesIO(data)) as opened:
                image = opened.convert("RGB")
        except (UnidentifiedImageError, OSError):
            log.warning("reference picture %s could not be opened; skipped", path.name)
            continue
        image.thumbnail((settings.reference_max_side, settings.reference_max_side))
        caption = f"Image de référence {len(chosen) + 1} : le même formulaire rempli pour une autre personne"
        chosen.append((digest, Picture(caption, _png(image), settings.reference_detail)))
    return chosen, refused_count


def request_key(record: DocumentRecord, model: str, prompt: Prompt, text: str, turns: dict[int, int],
                settings: InspectSettings, references: Sequence[str]) -> str:
    """Key of the disk cache: fingerprint, prompt version, model, and what was sent (settings, turns, references)."""
    payload = ["inspect", record.fingerprint, record.output_key, model, prompt.version, settings.image_detail,
               settings.reference_detail, settings.reference_max_side, settings.tile_rows, settings.tile_cols,
               settings.tile_overlap, settings.max_tiles, sorted(turns.items()), list(references),
               hashlib.sha256(text.encode("utf-8")).hexdigest()]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()[:32]


def inspect_document(
    record: DocumentRecord,
    ingest: IngestSettings,
    settings: InspectSettings,
    prompt: Prompt,
    types: Sequence[DocType],
    gateway: Gateway,
    model: str,
    reading: Reading | None = None,
    refused: set[str] | None = None,
) -> tuple[Inspection, list[GatewayAnswer], int]:
    """Inspect one document. Returns the inspection, the gateway's answers and the references refused."""
    doc_type = doc_type_for(record.nature, types)
    base = {"id": record.id, "doc_type": doc_type.name if doc_type else None, "model": model,
            "prompt_version": prompt.version, "fingerprint": record.fingerprint}
    if not record.pages:
        return Inspection(status="no_inspection", reason="no page picture (ingest error)", inspected_at=datetime.now(UTC),
                          **base), [], 0
    turns = page_turns(record, reading)
    pages = [(page.number, turned_picture(ingest, record, page.number, turns[page.number]) if page.number in turns
              else ingest.resolve(page.picture)) for page in record.pages]
    references, refused_count = reference_pictures(settings, doc_type, refused or set())
    rows, cols = settings.tile_rows, settings.tile_cols
    per_page = rows * cols if rows * cols > 1 else 0
    tile_count = min(len(pages), settings.max_tiles // per_page) * per_page if per_page else 0
    text = user_text(prompt, record.nature, doc_type, len(pages), tile_count, len(references))

    def messages() -> list[dict]:
        content: list[dict] = [{"type": "text", "text": text}]
        for number, path in pages:
            content += [{"type": "text", "text": f"Page {number}, entière"}, picture_part(path, settings.image_detail)]
        for picture in [*zoom_tiles(pages, settings), *(picture for _, picture in references)]:
            content += [{"type": "text", "text": picture.caption}, picture.part()]
        return [{"role": "system", "content": prompt.system}, {"role": "user", "content": content}]

    request = ReadRequest(
        doc_id=record.id,
        key=request_key(record, model, prompt, text, turns, settings, [digest for digest, _ in references]),
        model=model,
        prompt_version=prompt.version,
        messages=messages,
        schema=InspectionAnswer,
        stage="inspect",
    )
    answer = gateway.ask(request)
    inspection = Inspection(
        status="inspected" if answer.status == "read" else "no_inspection",
        reason=answer.reason,
        answer=answer.answer if answer.status == "read" else None,
        turns=turns, tiles=tile_count, references=len(references), calls=answer.calls,
        input_tokens=answer.input_tokens, output_tokens=answer.output_tokens, inspected_at=datetime.now(UTC), **base,
    )
    return inspection, [answer], refused_count


def _set_fingerprints(ingest: IngestSettings) -> set[str]:
    """Fingerprints of every document of the set, so that none of them is ever sent as a reference picture."""
    try:
        return {entry.fingerprint for entry in load_index(ingest).documents if entry.fingerprint}
    except FileNotFoundError:
        return set()


def run_inspect(
    ingest: IngestSettings,
    settings: InspectSettings,
    connection: ReadSettings,
    *,
    only: Sequence[str] = (),
    limit: int | None = None,
    replay: bool = False,
    force: bool = False,
    gateway: Gateway | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> InspectRun:
    """Inspect the documents of the index (all, ``only`` some, or the first ``limit``) and save each inspection.

    ``connection`` holds the Azure settings shared with the reading. Raises ``ReadSetupError``
    or ``GatewaySetupError`` when the run cannot start.
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
            missing = missing_configuration(connection)
            if missing:
                raise GatewaySetupError("Azure is not configured; missing in .env: " + "; ".join(missing))
            bounded = connection.model_copy(update={"max_completion_tokens": settings.max_completion_tokens})
            gateway = AzureGateway(bounded, cache, TokenLog(ingest.cache_path / TOKEN_LOG), force=force)
    model = connection.deployment or UNSET_MODEL
    refused = _set_fingerprints(ingest)
    run = InspectRun(replay=replay, model=model)
    for position, record in enumerate(records, start=1):
        if progress:
            progress(position, len(records), record.id)
        reading = load_reading(ingest, record.id)  # the document's own reading: only its page turns are used
        inspection, answers, refused_count = inspect_document(record, ingest, settings, prompt, types, gateway, model,
                                                              reading, refused)
        run.references_refused += refused_count
        run.live_calls += sum(answer.calls for answer in answers)
        run.from_cache += sum(answer.cached for answer in answers)
        run.input_tokens += sum(answer.input_tokens for answer in answers if not answer.cached)
        run.output_tokens += sum(answer.output_tokens for answer in answers if not answer.cached)
        if inspection.status == "no_inspection" and inspection.reason not in DOCUMENT_REASONS and record.pages:
            # The run could not ask (answer not saved for replay, network failure): nothing is saved,
            # and an earlier inspection stays as it was.
            run.not_inspected.append(record.id)
            continue
        save_inspection(ingest, inspection)
        run.inspections.append(inspection)
    run.seconds = time.perf_counter() - started
    return run
