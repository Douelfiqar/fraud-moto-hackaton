"""Shapes of what Ingest writes: one record per document, the text layer of text PDFs, and the index.

Everything is JSON on disk. Paths inside records are written relative to the
repository root with forward slashes (``data/documents/documents/DOC-042.png``),
or absolute when the file lives outside the repository.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

#: Version of the processing and of the files it writes. Bump it whenever either
#: changes: every cached record made by an older version is then made again.
INGEST_VERSION = "4"

Family = Literal["licence", "releve", "identity", "carte_grise", "rib"]
FAMILIES: tuple[str, ...] = get_args(Family)

#: ``picture``: a photo or scan file. ``pdf_scan``: a PDF without real text
#: (usually a scan wrapped in a PDF). ``pdf_text``: a PDF that carries real text.
Kind = Literal["picture", "pdf_scan", "pdf_text"]

#: How a page picture was obtained. ``original``: the received file itself, used
#: as it is. ``working_copy``: a lighter or more common copy of the received
#: picture. ``rendered``: a PDF page drawn into a picture.
PageSource = Literal["original", "working_copy", "rendered"]


class Code(StrEnum):
    """Every warning and error Ingest can attach to a document or to a run."""

    # Warnings about the document's context. They are worked out again on every
    # run, even when the document itself is taken from the cache.
    NO_CATALOGUE_ROW = "no_catalogue_row"
    NATURE_MISSING = "nature_missing"
    NATURE_UNMAPPED = "nature_unmapped"
    CATALOGUE_FILE_MISMATCH = "catalogue_file_mismatch"
    DUPLICATE_ID = "duplicate_id"
    NO_ID_IN_NAME = "no_id_in_name"
    # Warnings about the file itself.
    EXTENSION_MISMATCH = "extension_mismatch"
    EXTRA_FRAMES_IGNORED = "extra_frames_ignored"
    PAGES_TRUNCATED = "pages_truncated"
    ORIENTATION_TAG = "orientation_tag"
    WORKING_COPY_HEAVY = "working_copy_heavy"
    PDF_ENCRYPTED = "pdf_encrypted"
    PDF_STRUCTURE = "pdf_structure"
    PDF_ANNOTATIONS = "pdf_annotations"
    INVISIBLE_TEXT = "invisible_text"
    TEXT_FAILED = "text_failed"
    # Errors: the document could not be fully processed.
    EMPTY_FILE = "empty_file"
    UNREADABLE_FILE = "unreadable_file"
    UNSUPPORTED_FORMAT = "unsupported_format"
    CORRUPT_FILE = "corrupt_file"
    PASSWORD_PROTECTED = "password_protected"
    TOO_LARGE = "too_large"
    PAGE_FAILED = "page_failed"
    INTERNAL_ERROR = "internal_error"
    # Notices about the run as a whole (written in the index, not in records).
    CATALOGUE_UNAVAILABLE = "catalogue_unavailable"
    CATALOGUE_SHEET_MISSING = "catalogue_sheet_missing"
    CATALOGUE_DUPLICATE_ROW = "catalogue_duplicate_row"
    TARGET_NOT_FOUND = "target_not_found"
    OUTSIDE_DOCUMENTS_FOLDER = "outside_documents_folder"


#: Warnings that depend on the spreadsheet or on the other files, not on the file's bytes.
CONTEXT_CODES = frozenset(
    {
        Code.NO_CATALOGUE_ROW,
        Code.NATURE_MISSING,
        Code.NATURE_UNMAPPED,
        Code.CATALOGUE_FILE_MISMATCH,
        Code.DUPLICATE_ID,
        Code.NO_ID_IN_NAME,
    }
)


class Notice(BaseModel):
    """A warning or an error: a stable code for programs and a sentence for people.

    Messages never quote the document's content.
    """

    model_config = ConfigDict(frozen=True)

    code: Code
    message: str


class Barcode(BaseModel):
    """A barcode or two-dimensional code read on a page by the local reader."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="Symbology, as the reader names it: DataMatrix, QRCode, Code128...")
    text: str = Field(description="What the code holds, as text; control characters are kept.")


class PrintedWord(BaseModel):
    """A word read on a page by the local text reader, letter by letter."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="The word as read, with its case and accents.")
    confidence: float = Field(ge=0, le=1, description="The reader's confidence for the line the word belongs to.")


class Page(BaseModel):
    """One page of a document, as a picture the reader can be given."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, description="Page number, starting at 1.")
    picture: str = Field(description="Path of the page picture.")
    width: int = Field(ge=1, description="Width of the page picture, in pixels.")
    height: int = Field(ge=1, description="Height of the page picture, in pixels.")
    source: PageSource
    reasons: list[str] = Field(
        default_factory=list,
        description="Why a working copy was made: format, frames, bytes, pixels, colour_mode, "
        "transparency, orientation_tag. Empty for other sources.",
    )
    scale: float | None = Field(
        None, description="Working copies only: pixels of the copy per pixel of the original."
    )
    dpi: float | None = Field(
        None, description="Rendered pages only: pixels per inch of paper used to draw the page."
    )
    text_chars: int | None = Field(
        None, description="PDF pages only: non-blank characters of real text on the page."
    )
    upright: bool | None = Field(
        None,
        description="True when the PDF's own text runs left to right on the page picture; "
        "null when that cannot be told from the file.",
    )
    pixel_size: tuple[int, int] | None = Field(
        None,
        description="The picture's own size in pixels (width, height): the received picture file's, or for a PDF "
        "page holding exactly one embedded picture, that picture's; null for a PDF page with none or several.",
    )
    visible_chars: int | None = Field(
        None,
        description="PDF pages only: the characters of text_chars a reader of the page sees. The invisible text "
        "layer a scanner adds to make its picture searchable is left out.",
    )
    overlay_shapes: int | None = Field(
        None,
        description="Scanned pages only (a page holding a picture, in a PDF without real text): filled shapes drawn "
        "on the page, a plain white page background left out. A scanner draws none: any shape was laid on the scan "
        "afterwards. Null for a page that holds no picture.",
    )
    overlay_notes: list[str] = Field(
        default_factory=list,
        description="PDF pages only: kinds of the notes and form fields laid on the page (Ink, Line, FreeText...).",
    )
    barcodes: list[Barcode] | None = Field(
        None,
        description="Codes read on the page picture by the local reader (zxing-cpp): null when the reader is not "
        "installed or is turned off, empty when it found none.",
    )
    printed_words: list[PrintedWord] | None = Field(
        None,
        description="Long words read on the page picture by the local text reader (RapidOCR), which returns the "
        "letters it sees without correcting them: null when the reader is not installed, is turned off or does not "
        "read this family of documents.",
    )
    scanner_layers: bool | None = Field(
        None,
        description="Pages of a PDF without real text only: true when the page is stored the way office scanners "
        "save it, a background picture plus one-bit masks holding the text. Characters then differ in sharpness and "
        "weight from one layer to the other, without any retouching.",
    )


class TextSpan(BaseModel):
    """A piece of text in a PDF that shares one font, size and colour."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    text: str
    font: str = Field(description="Font name, without any subset prefix such as 'ABCDEF+'.")
    size: float = Field(description="Font size, in points.")
    color: str = Field(description="Text colour, as #rrggbb.")
    bbox: tuple[float, float, float, float] = Field(
        description="Position on the page picture, in pixels: left, top, right, bottom."
    )
    visible: bool = Field(True, description="False for text drawn invisibly, as scanners do.")
    origin: tuple[float, float] | None = Field(
        None, description="Start of the baseline (the line the letters sit on), in pixels of the page picture."
    )
    order: int | None = Field(None, description="Drawing order on the page: a higher number is drawn later.")


class Cover(BaseModel):
    """An opaque shape or picture drawn after some text and touching it: what could hide that text."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    order: int = Field(description="Drawing order on the page, in the same numbering as the pieces of text.")
    bbox: tuple[float, float, float, float] = Field(description="Position on the page picture, in pixels.")
    kind: Literal["shape", "picture"]
    page_share: float = Field(description="Share of the page area it covers (a scan laid over the page is near 1).")


class TextLayer(BaseModel):
    """Every piece of real text of a ``pdf_text`` document, saved next to its record."""

    model_config = ConfigDict(extra="forbid")

    id: str
    bbox_units: Literal["page_picture_px"] = "page_picture_px"
    spans: list[TextSpan] = Field(default_factory=list)
    covers: list[Cover] = Field(default_factory=list)


class DocumentRecord(BaseModel):
    """What Ingest knows about one document. Later steps read this instead of the file."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Organisers' id taken from the file name, such as DOC-042.")
    source_file: str = Field(description="Path of the original file, never copied or modified.")
    nature: str | None = Field(None, description="Nature given by the organisers' spreadsheet.")
    family: Family | None = Field(None, description="Family derived from the nature.")
    fingerprint: str | None = Field(None, description="SHA-256 of the file bytes.")
    size_bytes: int | None = None
    file_format: str | None = Field(
        None, description="Format found in the file's content (not its extension): pdf, png, jpeg..."
    )
    kind: Kind | None = Field(None, description="Null when the file could not be read at all.")
    pages: list[Page] = Field(default_factory=list)
    upright: bool | None = Field(
        None, description="True for a PDF whose own text runs horizontally on every page; else null."
    )
    text: str | None = Field(None, description="pdf_text only: path of the text layer file.")
    fonts: dict[str, int] | None = Field(
        None, description="pdf_text only: non-blank characters per font name."
    )
    warnings: list[Notice] = Field(default_factory=list)
    error: Notice | None = Field(None, description="Set when the document could not be fully processed.")
    output_key: str | None = Field(
        None, description="Digest of the settings and code version that produced the outputs."
    )
    ingest_version: str = INGEST_VERSION
    ingested_at: datetime | None = None


class IndexEntry(BaseModel):
    """One line of the index: enough to choose documents without opening every record."""

    model_config = ConfigDict(extra="forbid")

    id: str
    record: str = Field(description="Path of the record file.")
    source_file: str
    fingerprint: str | None
    kind: Kind | None
    family: Family | None
    nature: str | None
    pages: int
    warnings: list[Code] = Field(default_factory=list)
    error: Code | None = None

    @classmethod
    def from_record(cls, record: DocumentRecord, record_path: str) -> IndexEntry:
        return cls(
            id=record.id,
            record=record_path,
            source_file=record.source_file,
            fingerprint=record.fingerprint,
            kind=record.kind,
            family=record.family,
            nature=record.nature,
            pages=len(record.pages),
            warnings=[w.code for w in record.warnings],
            error=record.error.code if record.error else None,
        )


class IgnoredFile(BaseModel):
    """A file or folder found in the documents folder that is not a document."""

    path: str
    reason: str


class IngestIndex(BaseModel):
    """The list of all records, so later steps can go through them without scanning folders."""

    model_config = ConfigDict(extra="forbid")

    ingest_version: str = INGEST_VERSION
    updated_at: datetime
    documents_dir: str
    catalogue: str | None
    documents: list[IndexEntry] = Field(default_factory=list)
    catalogue_rows_without_file: list[str] = Field(default_factory=list)
    ignored: list[IgnoredFile] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
