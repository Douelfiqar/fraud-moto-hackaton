"""Typography inside a text PDF: baseline, size, covered text; generated PDFs only."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from conftest import REPO_ROOT, make_catalogue, make_settings
from fraudcheck.checks.base import registered
from fraudcheck.checks.runner import check_document, document_context
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.ingest.pipeline import load_record, run_ingest

FONTS = ("helv", "tiro")  # alternating fonts keep each piece its own span in the text layer
WORDS = ["Contrat numéro", "souscrit le", "pour le véhicule", "désigné ci-dessous"]


def line_of_pieces(page, y, sizes=None, shifts=None, colours=None, words=WORDS, x0=60, gap=12):
    """One printed line of separate pieces, each a short gap after the previous one."""
    x = x0
    for position, word in enumerate(words):
        size = sizes[position] if sizes else 11
        dy = shifts[position] if shifts else 0
        colour = colours[position] if colours else (0, 0, 0)
        font = FONTS[position % 2]
        page.insert_text((x, y + dy), word, fontname=font, fontsize=size, color=colour)
        x += pymupdf.get_text_length(word, fontname=font, fontsize=size) + gap


def build(path, draw) -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for row in range(6):  # ordinary lines, the same in every test document
        line_of_pieces(page, 100 + row * 30)
    draw(page)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def outcome(workspace, draw, check: str) -> tuple[str | None, dict]:
    settings = make_settings(workspace)
    build(settings.documents_path / "DOC-1.pdf", draw)
    make_catalogue(settings.catalogue_file, [("DOC-1", "DOC-1.pdf", "Relevé d'information (relevé de sinistres)")])
    run_ingest(settings)
    context = document_context(settings, CheckSettings(root=workspace, doc_types_dir=REPO_ROOT / "config" / "doc_types"),
                               load_record(settings, "DOC-1"))
    result = check_document(context, {check: registered()[check]}).get(check)
    return (result.outcome, result.details) if result else (None, {})


def nothing(page) -> None:
    line_of_pieces(page, 400)


@pytest.mark.parametrize("check", ["baseline_shift", "covered_text"])
def test_an_ordinary_text_pdf_passes_every_typography_check(workspace, check) -> None:
    assert outcome(workspace, nothing, check)[0] == "pass"


def test_a_piece_off_the_baseline_of_agreeing_neighbours_fails(workspace) -> None:
    result, details = outcome(workspace, lambda page: line_of_pieces(page, 400, shifts=[0, 0, 1.0, 0]), "baseline_shift")
    assert result == "fail" and len(details["pieces"]) == 1 and 0.08 < details["pieces"][0]["shift"] < 0.1


def test_two_pieces_alone_cannot_tell_which_one_is_off(workspace) -> None:
    pair = lambda page: line_of_pieces(page, 400, shifts=[0, 1.0], words=WORDS[:2])  # noqa: E731
    assert outcome(workspace, pair, "baseline_shift")[0] == "pass"


def test_a_superscript_is_not_a_shift(workspace) -> None:
    raised = lambda page: line_of_pieces(page, 400, shifts=[0, 0, -5.0, 0])  # noqa: E731  (45% of the letter height)
    assert outcome(workspace, raised, "baseline_shift")[0] == "pass"


def _white_box_over_old_text(page) -> None:
    page.insert_text((60, 400), "Ancienne valeur", fontname="helv", fontsize=11)
    page.draw_rect(pymupdf.Rect(55, 388, 200, 404), color=None, fill=(1, 1, 1))
    page.insert_text((60, 400), "Nouvelle", fontname="helv", fontsize=11)


def test_text_hidden_under_a_white_box_drawn_after_it_fails(workspace) -> None:
    result, details = outcome(workspace, _white_box_over_old_text, "covered_text")
    assert result == "fail" and len(details["hidden"]) == 1


def test_a_see_through_highlight_hides_nothing(workspace) -> None:
    def highlight(page) -> None:
        page.insert_text((60, 400), "Valeur surlignée", fontname="helv", fontsize=11)
        page.draw_rect(pymupdf.Rect(55, 388, 200, 404), color=None, fill=(1, 1, 0), fill_opacity=0.3)

    assert outcome(workspace, highlight, "covered_text")[0] == "pass"


def test_a_scan_laid_over_its_own_text_layer_is_normal(workspace) -> None:
    def scan(page) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (60, 80), "white").save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())

    assert outcome(workspace, scan, "covered_text")[0] == "pass"


def test_two_different_texts_printed_on_each_other_fail(workspace) -> None:
    def stacked(page) -> None:
        page.insert_text((60, 400), "01/02/2020", fontname="helv", fontsize=11)
        page.insert_text((61, 400), "09/08/2021", fontname="tiro", fontsize=11)

    result, details = outcome(workspace, stacked, "covered_text")
    assert result == "fail" and len(details["stacked"]) == 1


def test_a_piece_redrawn_inside_a_longer_one_is_a_writer_habit(workspace) -> None:
    def redrawn(page) -> None:
        page.insert_text((60, 400), "Période du contrat en cours", fontname="helv", fontsize=11)
        page.insert_text((60, 400), "Période du", fontname="helv", fontsize=11)

    assert outcome(workspace, redrawn, "covered_text")[0] == "pass"


def test_the_text_layer_keeps_baselines_drawing_order_and_covers(workspace) -> None:
    settings = make_settings(workspace)
    build(settings.documents_path / "DOC-1.pdf", _white_box_over_old_text)
    run_ingest(settings)
    context = document_context(settings, CheckSettings(root=workspace), load_record(settings, "DOC-1"))
    spans = context.text_layer.spans
    assert all(span.origin is not None and span.order is not None for span in spans)
    assert all(span.origin[1] >= span.bbox[1] for span in spans)  # the baseline lies inside the letters' box
    [cover] = context.text_layer.covers
    assert cover.kind == "shape" and cover.page_share < 0.1
    old = next(span for span in spans if span.text.startswith("Ancienne"))
    new = next(span for span in spans if span.text.startswith("Nouvelle"))
    assert old.order < cover.order < new.order
