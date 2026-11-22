"""Readers that run on the laptop, both optional: codes (zxing-cpp) and printed words (RapidOCR).

What they read is recorded by Ingest for each page; the checks only use those facts.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from conftest import drawn_picture, make_catalogue, make_settings, save_picture
from fraudcheck.ingest import barcodes, words
from fraudcheck.ingest.barcodes import read_barcodes
from fraudcheck.ingest.models import Barcode, PrintedWord
from fraudcheck.ingest.pipeline import load_record, run_ingest
from fraudcheck.ingest.words import read_words

#: A made-up 2D-Doc: header, two data fields separated by the group separator, then the signature.
TWOD_DOC = "DC04FR000001FFFFFFFF1301FR60ESSAI PREMIER\x1d62TESTEUR\x1d\x1fSIGNATURE"


# --- codes ----------------------------------------------------------------------------------------------------


def test_without_the_code_reader_a_page_says_it_was_not_looked_at(workspace, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "zxingcpp", None)  # importing it now fails, as on a machine without the library
    picture = save_picture(drawn_picture(), workspace / "page.png")
    assert read_barcodes(picture) is None and not barcodes.reader_installed()


def test_a_picture_that_cannot_be_opened_gives_no_answer(workspace) -> None:
    pytest.importorskip("zxingcpp")
    broken = workspace / "broken.png"
    broken.write_bytes(b"not a picture")
    assert read_barcodes(broken) is None


def test_a_printed_code_is_read_back_with_its_separators(workspace) -> None:
    zxingcpp = pytest.importorskip("zxingcpp")
    code = zxingcpp.create_barcode(TWOD_DOC, zxingcpp.BarcodeFormat.DataMatrix)
    drawn = Image.fromarray(np.array(zxingcpp.write_barcode_to_image(code, scale=4)))
    page = Image.new("RGB", (900, 600), "white")
    page.paste(drawn, (40, 60))
    assert read_barcodes(save_picture(page, workspace / "with-code.png")) == [Barcode(kind="DataMatrix", text=TWOD_DOC)]
    assert read_barcodes(save_picture(drawn_picture((300, 200)), workspace / "without.png")) == []


# --- printed words --------------------------------------------------------------------------------------------


def test_without_the_text_reader_a_page_says_it_was_not_read(workspace, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    words._engine.cache_clear()
    try:
        assert read_words(save_picture(drawn_picture(), workspace / "page.png")) is None and not words.reader_installed()
    finally:
        words._engine.cache_clear()  # the next test builds the real reader again, when it is installed


def test_the_text_reader_returns_the_letters_it_sees(workspace) -> None:
    """A word that is not a word comes back as printed: the reader corrects nothing."""
    pytest.importorskip("rapidocr_onnxruntime")
    page = Image.new("RGB", (1200, 400), "white")
    draw, font = ImageDraw.Draw(page), ImageFont.load_default(size=64)
    draw.text((60, 60), "IMMATRICULATION", fill="black", font=font)
    draw.text((60, 220), "IMMSTBICULATION", fill="black", font=font)
    read = {word.text.upper() for word in read_words(save_picture(page, workspace / "words.png"))}
    assert {"IMMATRICULATION", "IMMSTBICULATION"} <= read


# --- what Ingest records --------------------------------------------------------------------------------------


def test_ingest_records_codes_and_words_page_by_page_unless_turned_off(workspace, monkeypatch) -> None:
    codes = [Barcode(kind="DataMatrix", text=TWOD_DOC)]
    printed = [PrintedWord(text="CERTIFICAT", confidence=0.97)]
    monkeypatch.setattr("fraudcheck.ingest.pipeline.read_barcodes", lambda picture: codes)
    monkeypatch.setattr("fraudcheck.ingest.pipeline.read_words", lambda picture: printed)
    settings = make_settings(workspace)
    save_picture(drawn_picture((300, 200)), settings.documents_path / "DOC-1.png")
    save_picture(drawn_picture((200, 300)), settings.documents_path / "DOC-2.png")
    make_catalogue(settings.catalogue_file, [("DOC-1", None, "Carte grise"), ("DOC-2", None, "Relevé d'identité bancaire")])
    run_ingest(settings)
    official, bank = load_record(settings, "DOC-1").pages[0], load_record(settings, "DOC-2").pages[0]
    assert official.barcodes == codes and official.printed_words == printed
    assert bank.barcodes == codes and bank.printed_words is None  # a bank's own layout: no official wording to read
    off = make_settings(workspace, local_barcodes=False, local_words=False)
    assert off.output_key() == settings.output_key()  # the saved Azure answers stay valid either way
    run_ingest(off, force=True)
    page = load_record(off, "DOC-1").pages[0]
    assert page.barcodes is None and page.printed_words is None
