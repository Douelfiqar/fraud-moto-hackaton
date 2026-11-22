"""PDF files: draw every page into a picture, and keep the text with its fonts when there is real text.

A PDF is not a picture: it is a list of drawing instructions ("write this word in
this font at this place"). Each page is drawn into a grid of pixels at
``pdf_dpi`` pixels per inch of paper. When the PDF carries more than
``text_min_chars`` characters of real text, every piece of text is saved with its
font, size, colour and position, because a later check looks for pieces set in a
font the rest of the document does not use.
"""

from __future__ import annotations

import io
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from PIL import Image

from fraudcheck.ingest.models import Code, Cover, Kind, Notice, Page, TextLayer, TextSpan
from fraudcheck.ingest.pictures import encode_jpeg
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import TEXT_NAME, CacheWriteError, atomic_write_bytes, atomic_write_text

# MuPDF's own messages are collected into the record instead of being printed on the console.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

#: Text extraction options: keep ligatures and spaces as written, skip embedded images.
TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
#: Embedded fonts often carry a six-letter subset tag, as in ``ABCDEF+Arial``.
SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
PDF_EXTENSIONS = frozenset({".pdf"})
_PDF_MAGIC = b"%PDF-"
#: The PDF standard lets the header start anywhere in the first 1024 bytes.
_HEADER_WINDOW = 1024
_MESSAGE_LIMIT = 160
#: A fill this opaque or more hides what lies under it (PDF fill opacity runs from 0 to 1).
_OPAQUE = 0.99
#: Pillow's PNG compression level 3: on the 136 real pages it encoded faster than PyMuPDF's
#: own PNG writer (13 s against 19 s) and gave smaller files (117 MB against 155 MB).
_PNG_COMPRESSION = 3


def looks_like_pdf(head: bytes) -> bool:
    return _PDF_MAGIC in head[:_HEADER_WINDOW]


def clean_font_name(name: str | None) -> str:
    return SUBSET_PREFIX.sub("", (name or "").strip()) or "unknown"


def count_chars(text: str) -> int:
    """Characters other than spaces, tabs and line breaks."""
    return sum(1 for ch in text if not ch.isspace())


@dataclass
class PdfResult:
    kind: Kind | None = None
    pages: list[Page] = field(default_factory=list)
    upright: bool | None = None
    text: str | None = None
    fonts: dict[str, int] | None = None
    warnings: list[Notice] = field(default_factory=list)
    error: Notice | None = None
    messages: list[str] = field(default_factory=list)  # MuPDF's messages while opening, reading and drawing


def _own_picture_size(page: pymupdf.Page) -> tuple[int, int] | None:
    """The pixel size of the page's embedded picture when the page holds exactly one; ``None`` otherwise."""
    try:
        pictures = page.get_image_info(xrefs=True)
    except Exception:  # an odd page: no answer rather than a wrong one
        return None
    finally:
        _drain()  # messages of this analysis are not about the document's own reading
    if len(pictures) != 1:
        return None
    width, height = int(pictures[0].get("width") or 0), int(pictures[0].get("height") or 0)
    return (width, height) if width > 0 and height > 0 else None


def _drain() -> list[str]:
    """MuPDF's messages since the last call (the store is emptied)."""
    return [line for line in pymupdf.TOOLS.mupdf_warnings().splitlines() if line.strip()]


#: A filled shape this white, covering this share of the page, is only the page's background.
_WHITE = 0.97
_BACKGROUND_SHARE = 0.8
#: Compressions used for one-bit pictures: the text masks of a scanner's layered page.
_MASK_FILTERS = ("CCITTFaxDecode", "JBIG2Decode")


def _overlay_shapes(page: pymupdf.Page) -> int | None:
    """Filled shapes drawn on a scanned page, the plain white page background left out.

    A scanner writes a picture and nothing else. A shape on such a page (a white patch
    over a value, a black bar, a coloured block) was laid on the scan afterwards.
    ``None`` for a page that holds no picture: it is not a scanned page.
    """
    try:
        if not page.get_images(full=True):
            return None
        drawings = page.get_drawings()
    except Exception:  # an odd page: no answer rather than a wrong one
        return None
    finally:
        _drain()
    area = page.rect.get_area() or 1.0
    count = 0
    for drawing in drawings:
        fill = drawing.get("fill")
        if fill is None:
            continue
        rect = drawing.get("rect")
        share = (rect.get_area() / area) if rect is not None else 0.0
        if share >= _BACKGROUND_SHARE and all(part >= _WHITE for part in fill):
            continue  # the white sheet under the scan
        count += 1
    return count


def _scanner_layers(page: pymupdf.Page) -> bool | None:
    """True when the page is a background picture plus one-bit text masks, as office scanners save it."""
    try:
        pictures = page.get_images(full=True)
    except Exception:
        return None
    finally:
        _drain()
    masks = sum(1 for item in pictures if item[4] == 1 or item[8] in _MASK_FILTERS)
    return masks > 0 and len(pictures) > masks


@dataclass
class _PageText:
    chars: int = 0
    invisible_chars: int = 0
    upright: bool | None = None
    spans: list[dict[str, Any]] = field(default_factory=list)
    fonts: Counter[str] = field(default_factory=Counter)
    covers: list[dict[str, Any]] = field(default_factory=list)


Orders = dict[tuple[float, float], list[tuple[int, str]]]


def _drawing_orders(page: pymupdf.Page) -> Orders:
    """Drawing operations of the page's text (PyMuPDF's text trace): for each character's baseline
    point, the operations drawn there with their order and the text that follows from that point."""
    orders: Orders = {}
    try:
        for trace in page.get_texttrace():
            chars = trace.get("chars", ())
            text = "".join(chr(char[0]) if char[0] > 0 else " " for char in chars)
            for position, char in enumerate(chars):
                key = (round(char[2][0], 1), round(char[2][1], 1))
                orders.setdefault(key, []).append((int(trace["seqno"]), text[position:]))
    except Exception:  # an odd content stream: pieces simply get no order
        return {}
    return orders


def _common_start(one: str, two: str) -> int:
    count = 0
    for a, b in zip(one, two):
        if a != b:
            break
        count += 1
    return count


def _order_of(orders: Orders, origin: Any, text: str) -> int | None:
    """The order of the operation that draws this piece: same starting point, best matching text.

    Two pieces can start at the very same point (a value typed over another one); their
    text tells them apart.
    """
    if not origin:
        return None
    candidates = orders.get((round(origin[0], 1), round(origin[1], 1)))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (_common_start(item[1], text), item[0]))[0]


def _opaque_pictures(page: pymupdf.Page) -> set[tuple[float, float, float, float]]:
    """Boxes of the pictures drawn without a transparency mask (a stamp with a mask lets text show through)."""
    masked = {item[0]: item[1] for item in page.get_images(full=True)}  # xref -> soft-mask xref
    opaque = set()
    for info in page.get_image_info(xrefs=True):
        if masked.get(info.get("xref"), 0) == 0:
            opaque.add(tuple(round(value, 1) for value in info["bbox"]))
    return opaque


def _covers(page: pymupdf.Page, number: int, spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Opaque shapes and pictures drawn after some text and touching it, in the page's own frame.

    Facts only: whether a cover really hides a piece of text is judged by the check
    ``covered_text`` with its own thresholds. Order numbers are the positions in
    PyMuPDF's box log, the same numbering as the text trace and the drawings.
    """
    placed = [(span["order"], *span["bbox"]) for span in spans if span.get("order") is not None]
    if not placed:
        return []
    texts = np.array(placed, dtype=float)  # columns: order, x0, y0, x1, y1
    earliest = texts[:, 0].min()
    drawings = {drawing.get("seqno"): drawing for drawing in page.get_drawings()}
    pictures = _opaque_pictures(page)
    area = page.rect.get_area() or 1.0
    covers = []
    for order, (kind, box) in enumerate(page.get_bboxlog()):
        if order <= earliest or kind not in ("fill-path", "fill-image", "fill-shade"):
            continue
        x0, y0, x1, y1 = box
        before = texts[:, 0] < order
        touching = before & (texts[:, 1] < x1) & (texts[:, 3] > x0) & (texts[:, 2] < y1) & (texts[:, 4] > y0)
        if not touching.any():
            continue
        if kind == "fill-path":
            drawing = drawings.get(order)
            if drawing is None or drawing.get("fill") is None or (drawing.get("fill_opacity") or 1.0) < _OPAQUE:
                continue
        elif kind == "fill-image" and tuple(round(value, 1) for value in box) not in pictures:
            continue
        covers.append({"page": number, "order": order, "bbox": tuple(box),
                       "kind": "picture" if kind == "fill-image" else "shape",
                       "page_share": round(min(1.0, max(0.0, (x1 - x0) * (y1 - y0)) / area), 4)})
    return covers


def process_pdf(
    data: bytes,
    doc_id: str,
    out_dir: Path,
    settings: IngestSettings,
    display: Callable[[Path], str],
) -> PdfResult:
    """Render the pages of a PDF given as bytes and, for a text PDF, save its text layer.

    The bytes are opened from memory, which also avoids any trouble with accented paths.
    """
    result = PdfResult()
    pymupdf.TOOLS.reset_mupdf_warnings()
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # MuPDF raises several types for damaged files
        result.error = Notice(code=Code.CORRUPT_FILE, message=f"PDF could not be opened ({type(exc).__name__})")
        return result
    result.messages.extend(_drain())
    try:
        _process_document(document, doc_id, out_dir, settings, display, result)
    finally:
        document.close()
    messages = result.messages + _drain()
    if messages:
        first = " ".join(messages[0].split())[:_MESSAGE_LIMIT]
        result.warnings.append(
            Notice(
                code=Code.PDF_STRUCTURE,
                message=f"the PDF reader reported {len(messages)} problem(s) in the file's structure, "
                f"first: {first}",
            )
        )
    return result


def _process_document(
    document: pymupdf.Document,
    doc_id: str,
    out_dir: Path,
    settings: IngestSettings,
    display: Callable[[Path], str],
    result: PdfResult,
) -> None:
    if document.needs_pass and not document.authenticate(""):
        result.error = Notice(code=Code.PASSWORD_PROTECTED, message="the PDF needs a password to open")
        return
    if (document.metadata or {}).get("encryption"):
        result.warnings.append(
            Notice(code=Code.PDF_ENCRYPTED, message="the PDF is encrypted but opens without a password")
        )
    total = document.page_count
    if total == 0:
        result.error = Notice(code=Code.CORRUPT_FILE, message="the PDF has no page")
        return
    count = min(total, settings.max_pages)
    if total > count:
        result.warnings.append(
            Notice(
                code=Code.PAGES_TRUNCATED,
                message=f"{total} pages; only the first {count} are handled (setting max_pages)",
            )
        )

    texts = [_read_text(document, number, settings, result) for number in range(1, count + 1)]
    is_text = sum(page.chars for page in texts) > settings.text_min_chars
    result.kind = "pdf_text" if is_text else "pdf_scan"

    spans: list[TextSpan] = []
    covers: list[Cover] = []
    annotated: dict[int, list[str]] = {}
    for number, page_text in enumerate(texts, start=1):
        try:
            page = document.load_page(number - 1)
            zoom = _zoom(page, settings)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            result.messages.extend(_drain())
            data, extension = _encode_page(pixmap, settings)
            target = out_dir / f"page-{number}.{extension}"
            atomic_write_bytes(target, data)
            kinds = [annotation.type[1] for annotation in page.annots()]
            kinds += ["Widget"] * len(list(page.widgets()))
            result.pages.append(
                Page(
                    number=number,
                    picture=display(target),
                    width=pixmap.width,
                    height=pixmap.height,
                    source="rendered",
                    dpi=round(zoom * 72, 1),
                    text_chars=page_text.chars,
                    visible_chars=page_text.chars - page_text.invisible_chars,
                    upright=page_text.upright if is_text else None,
                    pixel_size=_own_picture_size(page),
                    overlay_shapes=None if is_text else _overlay_shapes(page),
                    overlay_notes=sorted(set(kinds)),
                    scanner_layers=None if is_text else _scanner_layers(page),
                )
            )
            if is_text:
                spans.extend(_spans_on_picture(page, zoom, page_text.spans))
                covers.extend(_covers_on_picture(page, zoom, page_text.covers))
            if kinds:
                annotated[number] = kinds
        except CacheWriteError:
            raise  # the cache cannot be written: not a problem of this document
        except Exception as exc:  # one damaged page must not lose the others
            if result.error is None:
                result.error = Notice(
                    code=Code.PAGE_FAILED,
                    message=f"page {number} could not be drawn ({type(exc).__name__}); other pages are kept",
                )

    if annotated:
        pages = ", ".join(str(number) for number in annotated)
        types = ", ".join(sorted({kind for kinds in annotated.values() for kind in kinds}))
        result.warnings.append(
            Notice(
                code=Code.PDF_ANNOTATIONS,
                message=f"annotations or form fields on page(s) {pages} ({types}): they are drawn on the "
                "page pictures but their text is not in the text layer",
            )
        )
    if not result.pages:
        result.kind = None
        return
    if is_text:
        fonts: Counter[str] = Counter()
        for page_text in texts:
            fonts.update(page_text.fonts)
        result.fonts = dict(sorted(fonts.items(), key=lambda item: (-item[1], item[0])))
        invisible = sum(page.invisible_chars for page in texts)
        if invisible > settings.text_min_chars:
            result.warnings.append(
                Notice(
                    code=Code.INVISIBLE_TEXT,
                    message=f"{invisible} characters of text are invisible, as in the text layer a "
                    "scanner adds; that text is a machine reading, not what is printed",
                )
            )
        target = out_dir / TEXT_NAME
        layer = TextLayer(id=doc_id, spans=spans, covers=covers)
        atomic_write_text(target, layer.model_dump_json(indent=1))
        result.text = display(target)
        complete = len(result.pages) == count
        result.upright = True if complete and all(page.upright for page in result.pages) else None


def _read_text(document: pymupdf.Document, number: int, settings: IngestSettings, result: PdfResult) -> _PageText:
    """Characters, fonts, pieces of text and direction of one page. A failure leaves the page without text."""
    page_text = _PageText()
    try:
        page = document.load_page(number - 1)
        content = page.get_text("dict", flags=TEXT_FLAGS)
        result.messages.extend(_drain())  # messages from reading the file's text: kept
        orders = _drawing_orders(page)
        matrix = page.rotation_matrix  # from the page's own frame to the picture's frame
        min_cos = math.cos(math.radians(settings.upright_max_angle_deg))
        upright_chars = 0
        for block in content.get("blocks", ()):
            for line in block.get("lines", ()):
                dx, dy = line.get("dir", (1.0, 0.0))
                runs_right = dx * matrix.a + dy * matrix.c >= min_cos
                for span in line.get("spans", ()):
                    text = span.get("text", "")
                    chars = count_chars(text)
                    if not chars:
                        continue
                    font = clean_font_name(span.get("font"))
                    visible = span.get("alpha", 255) != 0
                    page_text.chars += chars
                    page_text.fonts[font] += chars
                    if not visible:
                        page_text.invisible_chars += chars
                    if runs_right:
                        upright_chars += chars
                    origin = span.get("origin")
                    page_text.spans.append(
                        {
                            "text": text,
                            "font": font,
                            "size": round(float(span.get("size", 0.0)), 2),
                            "color": f"#{int(span.get('color', 0)) & 0xFFFFFF:06x}",
                            "bbox": span.get("bbox", (0.0, 0.0, 0.0, 0.0)),
                            "visible": visible,
                            "page": number,
                            "origin": origin,
                            "order": _order_of(orders, origin, text),
                        }
                    )
        if page_text.chars > settings.text_min_chars and upright_chars >= settings.upright_min_share * page_text.chars:
            page_text.upright = True
        page_text.covers = _covers(page, number, page_text.spans)
        _drain()  # messages raised by these measurements, not by the file's structure: dropped
    except Exception as exc:  # unreadable text on one page: keep the page picture, warn
        result.warnings.append(
            Notice(code=Code.TEXT_FAILED, message=f"text of page {number} could not be read ({type(exc).__name__})")
        )
        return _PageText()
    return page_text


def _zoom(page: pymupdf.Page, settings: IngestSettings) -> float:
    """Scale from PDF points (1/72 inch) to pixels: ``pdf_dpi``, lowered for very large pages."""
    long_side = max(page.rect.width, page.rect.height)
    if not long_side > 0:
        raise ValueError("the page has no size")
    zoom = settings.pdf_dpi / 72
    if long_side * zoom > settings.max_side_px:
        zoom = settings.max_side_px / long_side
    return zoom


def _encode_page(pixmap: pymupdf.Pixmap, settings: IngestSettings) -> tuple[bytes, str]:
    """PNG keeps every pixel; a page heavier than ``max_bytes`` that way is saved as JPEG instead."""
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=_PNG_COMPRESSION)
    if buffer.tell() <= settings.max_bytes:
        return buffer.getvalue(), "png"
    return encode_jpeg(image, settings.jpeg_quality), "jpg"


def _box_on_picture(box: Any, to_picture: pymupdf.Matrix) -> tuple[float, float, float, float]:
    rect = pymupdf.Rect(box) * to_picture
    rect.normalize()
    return round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1)


def _spans_on_picture(page: pymupdf.Page, zoom: float, raw_spans: list[dict[str, Any]]) -> list[TextSpan]:
    """Move each piece of text from the page's own frame to pixels of the page picture."""
    to_picture = page.rotation_matrix * pymupdf.Matrix(zoom, zoom)
    spans = []
    for raw in raw_spans:
        origin = None
        if raw.get("origin"):
            point = pymupdf.Point(raw["origin"]) * to_picture
            origin = (round(point.x, 2), round(point.y, 2))
        spans.append(
            TextSpan(
                page=raw["page"],
                text=raw["text"],
                font=raw["font"],
                size=raw["size"],
                color=raw["color"],
                bbox=_box_on_picture(raw["bbox"], to_picture),
                visible=raw["visible"],
                origin=origin,
                order=raw.get("order"),
            )
        )
    return spans


def _covers_on_picture(page: pymupdf.Page, zoom: float, raw_covers: list[dict[str, Any]]) -> list[Cover]:
    to_picture = page.rotation_matrix * pymupdf.Matrix(zoom, zoom)
    return [Cover(page=raw["page"], order=raw["order"], bbox=_box_on_picture(raw["bbox"], to_picture),
                  kind=raw["kind"], page_share=raw["page_share"]) for raw in raw_covers]
