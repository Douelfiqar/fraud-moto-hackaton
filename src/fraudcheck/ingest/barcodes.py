"""Barcodes and two-dimensional codes on a page, read on the laptop.

Some documents carry their own data in a code: a 2D-Doc (the DataMatrix that French
issuers sign and print on identity cards, prefecture attestations, tax notices or proofs
of address), a QR code, a bar code. The reader is zxing-cpp, a local library: the page
never leaves the machine and nothing is paid.

The library is optional. Without it a page says ``barcodes = null``, which is not the
same as "no code": the checks that need a code then stay silent.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from fraudcheck.ingest.models import Barcode

log = logging.getLogger(__name__)

#: When no code is found, a page whose longest side is under this many pixels is tried again at twice its size:
#: the dots of a small code can be under two pixels wide.
UPSCALE_BELOW_PX = 2400
#: Longest text kept for one code (a 2D-Doc holds a few hundred characters).
MAX_TEXT_CHARS = 4000


def reader_installed() -> bool:
    try:
        import zxingcpp  # noqa: F401
    except ImportError:
        return False
    return True


def read_barcodes(picture: Path) -> list[Barcode] | None:
    """The codes found on a page picture. ``None`` when the reader is not installed or the picture cannot be opened."""
    try:
        import zxingcpp
    except ImportError:
        return None
    try:
        with Image.open(picture) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError):
        return None
    found = _decode(zxingcpp, image)
    if not found and max(image.size) < UPSCALE_BELOW_PX:
        found = _decode(zxingcpp, image.resize((image.width * 2, image.height * 2), Image.LANCZOS))
    unique: dict[tuple[str, str], Barcode] = {}
    for code in found:
        unique.setdefault((code.kind, code.text), code)
    return list(unique.values())


def _decode(zxingcpp, image: Image.Image) -> list[Barcode]:
    # "Plain" keeps the control characters that separate the fields of a 2D-Doc, instead of spelling them "<GS>".
    try:
        results = zxingcpp.read_barcodes(image, text_mode=zxingcpp.TextMode.Plain)
    except Exception:  # a picture the reader cannot handle gives no code, never a failed document
        log.debug("the barcode reader failed on one picture", exc_info=True)
        return []
    return [Barcode(kind=getattr(item.format, "name", str(item.format)), text=item.text[:MAX_TEXT_CHARS])
            for item in results if item.text]
