"""Words printed on a page, read letter by letter on the laptop.

A language model reads like a person skimming: shown a caption with wrong letters, it
answers with the word it expects. A plain text reader has no such expectation and
returns the letters it sees. Ingest keeps the long words it reads, with the reader's
confidence, for the check that compares a form's own wording with the official one
(``official_wording``).

The reader is RapidOCR, a local library: the page never leaves the machine and nothing
is paid. It is optional: without it a page says ``printed_words = null``. Reading a page
takes a few seconds on a laptop, so only the families whose forms are official are read
(setting ``local_words_families``).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image

from fraudcheck.ingest.models import PrintedWord

log = logging.getLogger(__name__)

#: Shorter words are not kept: the check judges long words only, and short ones are mostly values.
MIN_LETTERS = 6
_WORD = re.compile(r"[^\W\d_]+")  # a run of letters, accents included


@lru_cache(maxsize=1)
def _engine():
    """The reader, built once; ``None`` when the library is not installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    return RapidOCR()


def reader_installed() -> bool:
    return _engine() is not None


def read_words(picture: Path) -> list[PrintedWord] | None:
    """The long words read on a page picture. ``None`` when the reader is not installed or the picture cannot be read."""
    engine = _engine()
    if engine is None:
        return None
    try:
        import numpy

        with Image.open(picture) as opened:
            pixels = numpy.asarray(opened.convert("RGB"))
        lines, _ = engine(pixels)
    except Exception:  # a picture the reader cannot handle gives no answer, never a failed document
        log.debug("the text reader failed on one picture", exc_info=True)
        return None
    words = []
    for _, text, confidence in lines or []:
        for word in _WORD.findall(str(text)):
            if len(word) >= MIN_LETTERS:
                words.append(PrintedWord(text=word, confidence=round(min(1.0, max(0.0, float(confidence))), 3)))
    return words
