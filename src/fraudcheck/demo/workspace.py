"""One uploaded document's own folder, outside the repository, laid out like the repository's inputs.

::

    <folder>/
      documents/<id>.<ext>      the uploaded file, as received
      catalogue.xlsx            one row: the document's id, file name and nature
      cache/                    what the stages write (record, page pictures, reading, checks, decision)
      review_decisions.csv      the handler's decision, in the format ``decide`` reads

The stages are the pipeline's own: they are only given settings whose folders point
here. Everything else (the ``.env`` file, ``config/``, the prompts) stays the
repository's, so a document is judged exactly as ``python -m fraudcheck run`` judges it.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from pathlib import Path

from fraudcheck.checks.settings import CheckSettings, load_check_settings
from fraudcheck.decision.settings import DecisionSettings, load_decision_settings
from fraudcheck.ingest.discover import organiser_id
from fraudcheck.ingest.settings import IngestSettings, load_settings
from fraudcheck.inspect.settings import InspectSettings, load_inspect_settings
from fraudcheck.read.settings import ReadSettings, load_read_settings

#: Formats the page accepts, with the extension the file is stored under.
EXTENSIONS = {"pdf": ".pdf", "png": ".png", "jpeg": ".jpg"}
#: Id of a document whose file name holds no organisers' id (DOC-042).
DEFAULT_ID = "document"
#: Longest side of a preview picture, in pixels: enough to zoom on a field, light enough to load at once.
PREVIEW_MAX_SIDE = 2000
_PDF_MAGIC, _PDF_WINDOW = b"%PDF-", 1024
_PNG_MAGIC, _JPEG_MAGIC = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"
_CATALOGUE_HEADERS = ("doc_id", "fichier", "nature du document")


def sniff_format(data: bytes) -> str | None:
    """``pdf``, ``png`` or ``jpeg`` from the file's first bytes; ``None`` for anything else."""
    if _PDF_MAGIC in data[:_PDF_WINDOW]:
        return "pdf"
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(_JPEG_MAGIC):
        return "jpeg"
    return None


def document_id(file_name: str) -> str:
    """The organisers' id when the file name holds one (DOC-042), a neutral id otherwise.

    Nothing else of the name is kept: a file name can hold a person's name.
    """
    return organiser_id(file_name) or DEFAULT_ID


def preview_jpeg(data: bytes, file_format: str, page: int = 1) -> bytes | None:
    """A light picture of one page, made in memory. ``None`` when the file cannot be drawn."""
    try:
        from PIL import Image

        if file_format == "pdf":
            import pymupdf

            with pymupdf.open(stream=data, filetype="pdf") as document:
                if not 1 <= page <= document.page_count:
                    return None
                sheet = document[page - 1]
                zoom = min(2.0, PREVIEW_MAX_SIDE / max(sheet.rect.width, sheet.rect.height, 1))
                pixmap = sheet.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        else:
            from PIL import ImageOps

            with Image.open(io.BytesIO(data)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")  # a phone photo shown the way it was taken
        return _jpeg(image)
    except Exception:  # a damaged file has no preview; the pipeline says why it cannot be read
        return None


def turned_jpeg(picture: bytes, degrees: int) -> bytes:
    """A preview picture turned by quarter turns, clockwise: a sideways page read the right way up."""
    from PIL import Image

    with Image.open(io.BytesIO(picture)) as opened:
        return _jpeg(opened.rotate(-(degrees // 90 * 90), expand=True))


def _jpeg(image) -> bytes:
    image.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


@dataclass(frozen=True)
class StageSettings:
    """The settings of every stage, all pointed at one document's folder."""

    ingest: IngestSettings
    reading: ReadSettings
    inspection: InspectSettings
    checks: CheckSettings
    decision: DecisionSettings


class Workspace:
    """The folder of one uploaded document."""

    def __init__(self, folder: Path, doc_id: str, file_format: str) -> None:
        self.folder, self.doc_id, self.file_format = folder, doc_id, file_format

    @property
    def file(self) -> Path:
        return self.folder / "documents" / f"{self.doc_id}{EXTENSIONS[self.file_format]}"

    @property
    def catalogue(self) -> Path:
        return self.folder / "catalogue.xlsx"

    @property
    def review_file(self) -> Path:
        return self.folder / "review_decisions.csv"

    def write_document(self, data: bytes) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(data)

    def write_catalogue(self, nature: str, sheet: str) -> None:
        """The one-row spreadsheet that gives the document its nature, as the organisers' spreadsheet does."""
        from openpyxl import Workbook

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = sheet
        worksheet.append(list(_CATALOGUE_HEADERS))
        worksheet.append([self.doc_id, self.file.name, nature])
        workbook.save(self.catalogue)
        workbook.close()

    def settings(self) -> StageSettings:
        """Every stage's settings, loaded as the command line loads them, with this folder's paths."""
        return StageSettings(
            ingest=load_settings({
                "documents_dir": self.file.parent, "catalogue_path": self.catalogue, "cache_dir": self.folder / "cache",
            }),
            reading=load_read_settings(),
            inspection=load_inspect_settings(),
            checks=load_check_settings(),
            # No Git call: nothing is deposited from here, so the commit of the answer file does not matter.
            decision=load_decision_settings({"review_decisions_path": self.review_file, "commit": "demo"}),
        )

    def remove(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)
