"""PDF files: pages drawn into pictures; text, fonts and direction kept for text PDFs."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from conftest import (
    RARE_TEXT,
    SAMPLE_TEXT,
    make_image_pdf,
    make_pdf,
    make_settings,
    make_text_pdf,
    noise_picture,
    picture_bytes,
)
from fraudcheck.ingest.models import Code, TextLayer
from fraudcheck.ingest.pdfs import clean_font_name, looks_like_pdf, process_pdf


def run(settings, path: Path, doc_id: str = "DOC-PDF"):
    out_dir = settings.cache_path / doc_id
    return process_pdf(path.read_bytes(), doc_id, out_dir, settings, settings.display)


def text_layer(settings, result) -> TextLayer:
    return TextLayer.model_validate_json(settings.resolve(result.text).read_bytes())


def dark_share(settings, page, bbox) -> float:
    """Share of dark pixels of the page picture inside ``bbox``."""
    image = Image.open(settings.resolve(page.picture)).convert("L")
    histogram = image.crop(tuple(round(value) for value in bbox)).histogram()
    return sum(histogram[:128]) / max(1, sum(histogram))


def test_text_pdf_keeps_fonts_text_positions_and_direction(settings) -> None:
    path = make_text_pdf(settings.documents_path / "DOC-1.pdf")
    result = run(settings, path)
    assert result.error is None and result.kind == "pdf_text"
    assert result.upright is True
    assert list(result.fonts) == ["Helvetica", "Courier"]
    assert result.fonts["Courier"] == len(RARE_TEXT.replace(" ", ""))
    [page] = result.pages
    assert page.source == "rendered" and page.dpi == 200.0 and page.upright is True
    assert (page.width, page.height) == (1653, 2339)  # A4 (595 x 842 points) at 200 pixels per inch
    assert Path(page.picture).name == "page-1.png"
    layer = text_layer(settings, result)
    assert layer.id == "DOC-PDF"
    assert {span.font for span in layer.spans} == {"Helvetica", "Courier"}
    first = next(span for span in layer.spans if span.text == SAMPLE_TEXT)
    assert first.page == 1 and first.size == 12 and first.color == "#000000" and first.visible
    assert dark_share(settings, page, first.bbox) > 0.05  # the box sits on the drawn letters


def test_pdf_with_only_a_picture_is_a_scan(settings) -> None:
    result = run(settings, make_image_pdf(settings.documents_path / "DOC-2.pdf"))
    assert result.kind == "pdf_scan" and result.error is None
    assert result.text is None and result.fonts is None and result.upright is None
    assert result.pages[0].upright is None and result.pages[0].text_chars == 0


def test_a_few_characters_are_not_real_text(settings) -> None:
    path = make_text_pdf(settings.documents_path / "DOC-3.pdf", lines=("Page 1",), rare=None)
    assert run(settings, path).kind == "pdf_scan"


def test_every_page_of_a_long_pdf_is_drawn(settings) -> None:
    result = run(settings, make_text_pdf(settings.documents_path / "DOC-4.pdf", pages=3))
    assert [Path(page.picture).name for page in result.pages] == ["page-1.png", "page-2.png", "page-3.png"]
    assert {span.page for span in text_layer(settings, result).spans} == {1, 2, 3}


def test_pages_beyond_the_limit_are_dropped_with_a_warning(workspace) -> None:
    settings = make_settings(workspace, max_pages=2)
    result = run(settings, make_image_pdf(settings.documents_path / "DOC-5.pdf", pages=3))
    assert len(result.pages) == 2
    assert [notice.code for notice in result.warnings] == [Code.PAGES_TRUNCATED]


def _sideways(page: pymupdf.Page) -> None:
    page.insert_text((72, 100), SAMPLE_TEXT, fontname="helv", fontsize=14)
    page.set_rotation(90)


def _turned_back(page: pymupdf.Page) -> None:
    page.insert_text((150, 700), SAMPLE_TEXT, fontname="helv", fontsize=14, rotate=90)
    page.set_rotation(90)


def _upside_down(page: pymupdf.Page) -> None:
    page.insert_text((150, 100), SAMPLE_TEXT, fontname="helv", fontsize=14, rotate=270)
    page.set_rotation(90)


@pytest.mark.parametrize(
    ("build", "upright", "box_is_wide"),
    [(_sideways, None, False), (_turned_back, True, True), (_upside_down, None, True)],
    ids=["text-sideways-on-picture", "rotated-page-reads-upright", "upside-down"],
)
def test_direction_and_positions_follow_the_page_rotation(settings, build, upright, box_is_wide) -> None:
    result = run(settings, make_pdf(settings.documents_path / "DOC-6.pdf", build))
    [page] = result.pages
    assert (page.width, page.height) == (2339, 1653)  # the rotated page is drawn landscape
    assert page.upright is upright and result.upright is upright
    [span] = text_layer(settings, result).spans
    left, top, right, bottom = span.bbox
    assert ((right - left) > (bottom - top)) is box_is_wide
    assert 0 <= left < right <= page.width and 0 <= top < bottom <= page.height
    assert dark_share(settings, page, span.bbox) > 0.05


def test_very_large_page_is_drawn_within_the_pixel_limit(workspace) -> None:
    settings = make_settings(workspace, max_side_px=1000)
    document = pymupdf.open()
    document.new_page(width=3000, height=2000).insert_text((100, 100), SAMPLE_TEXT, fontsize=40)
    path = settings.documents_path / "DOC-7.pdf"
    document.save(path)
    [page] = run(settings, path).pages
    assert (page.width, page.height) == (1000, 667)
    assert page.dpi == 24.0


def test_page_too_heavy_as_png_is_saved_as_jpeg(workspace) -> None:
    settings = make_settings(workspace, max_bytes=10_000)
    buffer = io.BytesIO()
    noise_picture((300, 300)).save(buffer, format="PNG")
    path = make_pdf(
        settings.documents_path / "DOC-12.pdf",
        lambda page: page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buffer.getvalue()),
    )
    [page] = run(settings, path).pages
    assert Path(page.picture).suffix == ".jpg" and page.source == "rendered"


def test_password_protected_pdf_is_reported_not_raised(settings) -> None:
    path = make_pdf(
        settings.documents_path / "DOC-8.pdf",
        lambda page: page.insert_text((72, 72), SAMPLE_TEXT),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="owner",
    )
    result = run(settings, path)
    assert result.error.code == Code.PASSWORD_PROTECTED and result.pages == []


def test_pdf_locked_only_against_editing_opens_with_a_warning(settings) -> None:
    path = make_pdf(
        settings.documents_path / "DOC-9.pdf",
        lambda page: page.insert_text((72, 72), SAMPLE_TEXT),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="",
    )
    result = run(settings, path)
    assert result.error is None and result.kind == "pdf_text"
    assert Code.PDF_ENCRYPTED in [notice.code for notice in result.warnings]


@pytest.mark.parametrize("content", [b"%PDF-1.7\n" + os.urandom(2000), b"%PDF-1.4\n%%EOF\n"])
def test_damaged_pdf_is_reported_not_raised(settings, content) -> None:
    path = settings.documents_path / "DOC-10.pdf"
    path.write_bytes(content)
    result = run(settings, path)
    assert result.error is not None and result.error.code == Code.CORRUPT_FILE
    assert result.pages == [] and result.kind is None


def test_annotations_and_invisible_text_are_reported(settings) -> None:
    def build(page: pymupdf.Page) -> None:
        page.insert_text((72, 100), SAMPLE_TEXT, fontname="helv")
        page.insert_text((72, 200), "Invisible words added under the picture by a scanner", render_mode=3)
        page.add_freetext_annot(pymupdf.Rect(72, 300, 300, 340), "Note")

    result = run(settings, make_pdf(settings.documents_path / "DOC-11.pdf", build))
    codes = [notice.code for notice in result.warnings]
    assert Code.PDF_ANNOTATIONS in codes and Code.INVISIBLE_TEXT in codes
    assert any(not span.visible for span in text_layer(settings, result).spans)


def test_what_is_laid_over_a_scanned_page_is_recorded(settings) -> None:
    def scan(page: pymupdf.Page) -> None:
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))  # the white sheet some scanners draw under the picture
        page.insert_image(pymupdf.Rect(50, 50, 545, 420), stream=picture_bytes())

    def retouched(page: pymupdf.Page) -> None:
        scan(page)
        page.draw_rect(pymupdf.Rect(100, 100, 180, 118), color=None, fill=(1, 1, 1))  # a white patch over a value
        page.insert_text((104, 114), "2019", fontname="helv", fontsize=11)  # another value typed on the patch
        page.insert_text((72, 600), "Invisible words", render_mode=3)  # a scanner's own reading: not an addition
        page.add_line_annot(pymupdf.Point(100, 300), pymupdf.Point(300, 300))

    [plain] = run(settings, make_pdf(settings.documents_path / "DOC-13.pdf", scan)).pages
    assert (plain.overlay_shapes, plain.visible_chars, plain.overlay_notes, plain.scanner_layers) == (0, 0, [], False)
    result = run(settings, make_pdf(settings.documents_path / "DOC-14.pdf", retouched), "DOC-14")
    [page] = result.pages
    assert result.kind == "pdf_scan" and (page.overlay_shapes, page.visible_chars, page.overlay_notes) == (1, 4, ["Line"])
    assert page.text_chars == 4 + len("Invisiblewords")
    # a page without any picture is not a scanned page: nothing can be laid over a scan there
    [no_picture] = run(settings, make_text_pdf(settings.documents_path / "DOC-15.pdf", lines=("Page 1",), rare=None)).pages
    assert no_picture.overlay_shapes is None
    # a PDF with real text is judged on its fonts: shapes are not counted as additions to a scan
    [typed] = run(settings, make_text_pdf(settings.documents_path / "DOC-16.pdf")).pages
    assert typed.overlay_shapes is None and typed.scanner_layers is None and typed.visible_chars == typed.text_chars


def test_a_scan_saved_in_layers_by_the_scanner_is_recognised(settings) -> None:
    """Office scanners save a background picture plus one-bit masks holding the text."""
    mask = io.BytesIO()
    Image.new("1", (400, 300), 1).save(mask, format="PDF")  # Pillow writes a one-bit picture as a fax-coded mask
    document = pymupdf.open(stream=mask.getvalue(), filetype="pdf")
    document[0].insert_image(document[0].rect, stream=picture_bytes())  # the background picture
    path = settings.documents_path / "DOC-17.pdf"
    document.save(path)
    [page] = run(settings, path).pages
    assert page.scanner_layers is True and page.overlay_shapes == 0


def test_font_subset_prefix_is_removed() -> None:
    assert clean_font_name("ABCDEF+ArialMT") == "ArialMT"
    assert clean_font_name("LiberationSans") == "LiberationSans"
    assert clean_font_name("Abcdef+Name") == "Abcdef+Name"  # not a subset tag: tags are six capitals
    assert clean_font_name("") == "unknown"


def test_pdf_header_may_follow_a_few_bytes_of_junk() -> None:
    assert looks_like_pdf(b"%PDF-1.7\n...")
    assert looks_like_pdf(b"\xef\xbb\xbf\r\n%PDF-1.4")
    assert not looks_like_pdf(b"\x89PNG\r\n\x1a\n")
