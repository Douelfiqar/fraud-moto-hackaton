"""Picture files (PNG, JPEG, TIFF, WEBP...): use the file as it is, or write a lighter working copy.

A picture is handed to the reader as it is when it is small enough and in a plain
form every decoder understands. Otherwise a working copy is written to the cache:
an 8-bit JPEG, laid on white paper if it had transparent parts, reduced if too big.
The pixels are never turned: which way is up is asked of the reader in a later step.
"""

from __future__ import annotations

import contextlib
import io
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from fraudcheck.ingest.models import Code, Notice, Page
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import CacheWriteError, atomic_write_bytes

with contextlib.suppress(ImportError):  # optional: phone photos in HEIC format
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()

#: Colour modes every common decoder reads. LA, RGBA and P are plain only without real transparency.
PLAIN_MODES = frozenset({"1", "L", "P", "RGB", "LA", "RGBA"})
#: Formats whose extra frames are further pages (multi-page scans); elsewhere they are animation.
PAGED_FORMATS = frozenset({"tiff"})
#: Formats Ingest recognises by their extension, to tell a damaged file from an unknown one.
PICTURE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".webp", ".gif", ".bmp", ".heic", ".heif"}
)
_ORIENTATION_TAG = 0x0112
_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})
_WHITE = (255, 255, 255)
_FALLBACK_QUALITY = 75


@dataclass
class PictureResult:
    pages: list[Page] = field(default_factory=list)
    warnings: list[Notice] = field(default_factory=list)
    error: Notice | None = None


@contextlib.contextmanager
def _pixel_ceiling(megapixels: float | None) -> Iterator[None]:
    """Let Pillow open pictures up to ``megapixels`` (``None``: any size) and refuse larger ones."""
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None if megapixels is None else int(megapixels * 1_000_000)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error" if megapixels else "ignore", Image.DecompressionBombWarning)
            yield
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def identify_picture(path: Path) -> str | None:
    """Picture format recognised in the file's content (``png``, ``jpeg``, ``tiff``...), or ``None``."""
    try:
        with _pixel_ceiling(None), Image.open(path) as image:
            name = (image.format or "").lower() or None
    except Exception:  # Pillow raises many types for content it does not recognise
        return None
    # Some cameras write "MPO" files: a normal JPEG followed by a small preview picture.
    return "jpeg" if name == "mpo" else name


def encode_jpeg(image: Image.Image, quality: int) -> bytes:
    """JPEG bytes of an RGB or greyscale picture. Full colour detail (4:4:4) keeps thin coloured strokes."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=0)
    return buffer.getvalue()


def has_transparency(image: Image.Image) -> bool:
    """True when some pixels are see-through (alpha channel below opaque, or a transparent colour)."""
    if image.mode in _ALPHA_MODES:
        lowest, _ = image.getchannel("A").getextrema()
        return lowest < 255
    return "transparency" in image.info


def to_plain(image: Image.Image) -> Image.Image:
    """An 8-bit RGB or greyscale copy, with any transparent parts laid on white paper."""
    if image.mode in _ALPHA_MODES or "transparency" in image.info:
        rgba = image.convert("RGBA")
        paper = Image.new("RGB", image.size, _WHITE)
        paper.paste(rgba, mask=rgba.getchannel("A"))
        return paper
    if image.mode in ("RGB", "L"):
        return image.copy()
    if image.mode == "1":
        return image.convert("L")
    if image.mode == "P":
        return image.convert("RGB")
    if image.mode.startswith("I;16"):  # 16-bit greys: Pillow's direct conversion clips them to white
        return image.convert("I").point(lambda value: value / 256).convert("L")
    if image.mode in ("I", "F"):  # 32-bit values: stretch the range actually used to 0-255
        low, high = image.getextrema()
        scale = 255.0 / (high - low) if high > low else 0.0
        return image.point(lambda value: (value - low) * scale).convert("L")
    return image.convert("RGB")  # CMYK, YCbCr, LAB, HSV...


def _orientation_tag(image: Image.Image) -> int | None:
    try:
        value = image.getexif().get(_ORIENTATION_TAG)
    except Exception:  # a damaged metadata block must not stop the picture
        return None
    return value if isinstance(value, int) else None


def process_picture(
    path: Path,
    file_format: str,
    size_bytes: int,
    out_dir: Path,
    settings: IngestSettings,
    display: Callable[[Path], str],
) -> PictureResult:
    """Describe each page of a picture file, writing working copies where needed."""
    result = PictureResult()
    limit = settings.max_open_megapixels
    try:
        with _pixel_ceiling(limit), Image.open(path) as image:
            width, height = image.size
            if width * height > limit * 1_000_000:
                raise Image.DecompressionBombError("picture above the size limit")
            frames = max(1, getattr(image, "n_frames", 1) or 1)
            if (image.format or "").upper() == "MPO":
                frames = 1  # the extra frame is a camera preview, not a page
            if file_format in PAGED_FORMATS:
                count = min(frames, settings.max_pages)
                if frames > count:
                    result.warnings.append(
                        Notice(
                            code=Code.PAGES_TRUNCATED,
                            message=f"{frames} pages; only the first {count} are handled (setting max_pages)",
                        )
                    )
            else:
                count = 1
                if frames > 1:
                    result.warnings.append(
                        Notice(
                            code=Code.EXTRA_FRAMES_IGNORED,
                            message=f"{frames} frames (an animation); only the first is used",
                        )
                    )
            tag = _orientation_tag(image)
            if tag not in (None, 1):
                result.warnings.append(
                    Notice(
                        code=Code.ORIENTATION_TAG,
                        message=f"orientation tag {tag}: viewers show this picture turned, but its pixels "
                        "are kept as stored and the working copy drops the tag",
                    )
                )
            for index in range(count):
                try:
                    image.seek(index)
                    result.pages.append(
                        _page_from_frame(image, index, frames, file_format, size_bytes, tag, path, out_dir,
                                         settings, display, result)
                    )
                except (Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError, CacheWriteError):
                    raise
                except Exception as exc:
                    if index == 0:
                        raise
                    result.error = Notice(
                        code=Code.PAGE_FAILED,
                        message=f"page {index + 1} could not be decoded ({type(exc).__name__}); "
                        f"the {index} page(s) before it are kept",
                    )
                    break
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError):
        result.pages.clear()
        result.error = Notice(
            code=Code.TOO_LARGE,
            message=f"picture larger than {limit:g} megapixels (setting max_open_megapixels); not opened",
        )
    except CacheWriteError:
        raise  # the cache cannot be written: not a problem of this document
    except Exception as exc:  # truncated or damaged content: Pillow raises many types
        result.pages.clear()
        result.error = Notice(code=Code.CORRUPT_FILE, message=f"picture could not be decoded ({type(exc).__name__})")
    return result


def _page_from_frame(
    image: Image.Image,
    index: int,
    frames: int,
    file_format: str,
    size_bytes: int,
    tag: int | None,
    path: Path,
    out_dir: Path,
    settings: IngestSettings,
    display: Callable[[Path], str],
    result: PictureResult,
) -> Page:
    image.load()  # decode now: a damaged file is caught here rather than by a later step
    width, height = image.size
    reasons = []
    if file_format not in settings.reader_formats:
        reasons.append("format")
    if frames > 1:
        reasons.append("frames")
    if size_bytes > settings.max_bytes:
        reasons.append("bytes")
    if max(width, height) > settings.max_side_px:
        reasons.append("pixels")
    if image.mode not in PLAIN_MODES:
        reasons.append("colour_mode")
    if has_transparency(image):
        reasons.append("transparency")
    if tag not in (None, 1):
        reasons.append("orientation_tag")
    number = index + 1
    if not reasons:
        return Page(number=number, picture=display(path), width=width, height=height, source="original",
                    pixel_size=(width, height))

    copy = to_plain(image)
    if max(copy.size) > settings.max_side_px:
        copy.thumbnail((settings.max_side_px, settings.max_side_px), Image.Resampling.LANCZOS, reducing_gap=3.0)
    data = encode_jpeg(copy, settings.jpeg_quality)
    if len(data) > settings.max_bytes:
        data = encode_jpeg(copy, min(settings.jpeg_quality, _FALLBACK_QUALITY))
        if len(data) > settings.max_bytes:
            result.warnings.append(
                Notice(
                    code=Code.WORKING_COPY_HEAVY,
                    message=f"page {number}: the working copy still weighs {len(data) / 1e6:.1f} MB",
                )
            )
    target = out_dir / f"page-{number}.jpg"
    atomic_write_bytes(target, data)
    return Page(
        number=number,
        picture=display(target),
        width=copy.width,
        height=copy.height,
        source="working_copy",
        reasons=reasons,
        scale=round(copy.width / width, 4),
        pixel_size=(width, height),
    )
