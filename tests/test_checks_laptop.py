"""Laptop checks: the font check, the check runner and the table, on generated documents only."""

from __future__ import annotations

import sys

import pytest

from conftest import REPO_ROOT, STAND_IN_FALLBACK, make_settings
from fraudcheck.checks.base import DocumentContext, registered
from fraudcheck.checks.laptop import official_wording
from fraudcheck.checks.laptop.font import is_fallback
from fraudcheck.checks.runner import ChecksSetupError, load_results, run_checks
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.ingest.models import DocumentRecord, Page, PrintedWord


def test_font_check_on_real_pdfs_uses_the_fallback_list(family_folder) -> None:
    default = run_checks(family_folder, CheckSettings(root=family_folder.root))
    assert default.results["DOC-202"]["font"].outcome == "pass"  # Courier is an ordinary font by default
    stand_in = CheckSettings(root=family_folder.root, fallback_fonts=(STAND_IN_FALLBACK,))
    courier = run_checks(family_folder, stand_in, only=["font"])
    failed = courier.results["DOC-202"]["font"]
    assert failed.outcome == "fail" and failed.details["pages"] == [1] and failed.details["pieces"] == 1
    assert "DOC-" not in failed.evidence  # the evidence speaks of this document only
    assert courier.results["DOC-201"]["font"].outcome == "pass"
    assert "font" not in courier.results["DOC-101"]  # pictures: the check does not apply


def test_results_are_saved_and_the_table_has_one_column_per_check(family_folder) -> None:
    run = run_checks(family_folder, CheckSettings(root=family_folder.root))
    header = run.table_path.read_text(encoding="utf-8-sig").splitlines()[0].split(";")
    assert header[:4] == ["id", "family", "kind", "nature"] and set(registered()) <= set(header)
    assert set(load_results(family_folder)) == set(run.results)
    assert (family_folder.cache_path / "DOC-202" / "checks.json").is_file()


def test_unknown_check_or_missing_index_is_a_setup_error(workspace, family_folder) -> None:
    with pytest.raises(ChecksSetupError, match="unknown"):
        run_checks(family_folder, CheckSettings(root=family_folder.root), only=["no_such_check"])
    with pytest.raises(ChecksSetupError, match="ingest"):
        run_checks(make_settings(workspace, cache_dir=workspace / "empty"), CheckSettings(root=workspace))


def _font_context(workspace, fonts: dict[str, int]) -> DocumentContext:
    page = Page(number=1, picture="x.png", width=10, height=10, source="rendered")
    record = DocumentRecord(id="DOC-1", source_file="x.pdf", kind="pdf_text", fonts=fonts, pages=[page])
    return DocumentContext(record=record, reading=None, text_layer=None, settings=CheckSettings(root=workspace))


@pytest.mark.parametrize(
    ("fonts", "outcome"),
    [
        ({"ArialMT": 6000, "Arial-BoldMT": 900, "LiberationSans": 174}, "fail"),
        ({"Roboto": 3000, "LiberationSans-Bold": 150}, "fail"),
        ({"LiberationSans": 3000}, "pass"),  # the whole document: produced with free software
        ({"LiberationSans": 2000, "ArialMT": 1000}, "pass"),  # a main font, not a patch
        ({"ArialMT": 3000, "OCRB": 40, "Courier": 60, "ZapfDingbats": 3, "Arial-ItalicMT": 80}, "pass"),
    ],
)
def test_font_rule(workspace, fonts, outcome) -> None:
    check = registered()["font"]
    assert check.run(_font_context(workspace, fonts)).outcome == outcome


def _scan_context(workspace, kind: str = "pdf_scan", **facts) -> DocumentContext:
    page = Page(number=1, picture="x.png", width=10, height=10, source="rendered", **facts)
    record = DocumentRecord(id="DOC-1", source_file="x.pdf", kind=kind, pages=[page])
    return DocumentContext(record=record, reading=None, text_layer=None, settings=CheckSettings(root=workspace))


@pytest.mark.parametrize(
    ("facts", "outcome"),
    [
        ({"overlay_shapes": 0, "visible_chars": 0}, "pass"),  # a scanner writes a picture and nothing else
        ({"overlay_shapes": 3, "visible_chars": 0}, "fail"),  # patches or bars drawn over the scan
        ({"overlay_shapes": 0, "visible_chars": 12}, "fail"),  # a few characters typed over it
        ({"overlay_shapes": 0, "visible_chars": 0, "overlay_notes": ["Ink", "Line"]}, "fail"),  # notes added to it
        ({"overlay_shapes": 0, "visible_chars": 0, "text_chars": 18}, "pass"),  # the scanner's own invisible text layer
        ({"overlay_shapes": None, "visible_chars": 6}, "cannot_tell"),  # no picture on the page: not a scan
        ({}, "cannot_tell"),  # a record ingested before these facts existed
    ],
)
def test_scan_overlay_rule(workspace, facts, outcome) -> None:
    result = registered()["scan_overlay"].run(_scan_context(workspace, **facts))
    assert result.outcome == outcome
    if outcome == "fail":
        assert result.clues and "posés après la numérisation" in result.evidence


def test_scan_overlay_judges_scanned_pdfs_only(workspace) -> None:
    check = registered()["scan_overlay"]
    assert check.applies(_scan_context(workspace))
    assert not check.applies(_scan_context(workspace, kind="pdf_text"))  # real text: the font checks speak
    assert not check.applies(_scan_context(workspace, kind="picture"))


def _wording_context(words, nature: str = "Carte grise", family: str = "carte_grise") -> DocumentContext:
    """A picture of an official form on which the local text reader read ``words`` (text, confidence)."""
    read = None if words is None else [PrintedWord(text=text, confidence=confidence) for text, confidence in words]
    page = Page(number=1, picture="x.png", width=10, height=10, source="original", printed_words=read)
    record = DocumentRecord(id="DOC-1", source_file="x.png", kind="picture", nature=nature, family=family, pages=[page])
    return DocumentContext(record=record, reading=None, text_layer=None, settings=CheckSettings(root=REPO_ROOT))


@pytest.mark.parametrize(
    ("words", "outcome"),
    [
        ([("CERTIFICAT", 0.97), ("IMMATRICULATION", 0.96)], "pass"),
        ([("CERTIFICAT", 0.97), ("MNSBICULATION", 0.90)], "fail"),  # letters swapped and fused, as a generator prints
        ([("NIMMATRICULATION", 0.95), ("CERTIFICATD", 0.95)], "pass"),  # the word glued to its neighbour
        ([("CERTIFICAT", 0.95), ("IMMATRLCULATION", 0.95), ("DELEGATLON", 0.95)], "pass"),  # I read as l: the reader's doing
        ([("CERTIFICAT", 0.95), ("MMATRICULATION", 0.95)], "pass"),  # cut at the edge of its box
        ([("CERTIFICAT", 0.95), ("IMMATRICURATION", 0.95)], "pass"),  # one wrong letter: reported, not failed
        ([("CERTIFICAT", 0.95), ("MPRINIERIE", 0.95)], "pass"),  # two wrong letters: still within a reader's own mistakes
        ([("CERTIFICAT", 0.95), ("MNSBICULATION", 0.60)], "pass"),  # the reader is not sure: a degraded copy
        ([("CERTIFICAT", 0.95), ("IDENTIFICATION", 0.95), ("DESIGNATION", 0.95)], "pass"),  # other words of the language
        ([("CERTIFICAT", 0.95), ("DESIGNATLON", 0.95)], "pass"),  # another word, misread by one letter
        ([("CERTIFICAT", 0.95), ("CERTIFICADO", 0.95), ("DEINMATRICULARE", 0.95)], "pass"),  # the title in other languages
        ([("MNSBICULATION", 0.60), ("AUTRECHOSE", 0.95)], "cannot_tell"),  # no official word found: nothing to judge
        ([], "cannot_tell"),
    ],
)
def test_official_wording_rule(words, outcome) -> None:
    pytest.importorskip("spellchecker")
    result = registered()["official_wording"].run(_wording_context(words))
    assert result.outcome == outcome
    if outcome == "fail":
        assert result.ai_generated and "« IMMATRICULATION » (page 1)" in result.evidence
        assert "MNSBICULATION" not in result.evidence  # what was read stays in the details
        assert result.details["damaged"]["IMMATRICULATION"]["read"] == "MNSBICULATION"
    if ("IMMATRICURATION", 0.95) in words:
        assert "une ou deux lettres" in result.evidence and "IMMATRICULATION" in result.details["slight"]
    if ("CERTIFICADO", 0.95) in words:
        assert not result.details["slight"]  # a word of another language is not even a near miss


def test_official_wording_needs_read_words_an_official_form_and_the_word_list(monkeypatch) -> None:
    check = registered()["official_wording"]
    read = [("CERTIFICAT", 0.97)]
    assert check.applies(_wording_context(read))
    assert not check.applies(_wording_context(None))  # the local reader did not read this page
    assert not check.applies(_wording_context(read, nature="Relevé d'identité bancaire", family="rib"))  # no official form
    monkeypatch.setitem(sys.modules, "spellchecker", None)  # the word list is not installed
    official_wording.dictionary.cache_clear()
    try:
        assert check.run(_wording_context(read)).outcome == "cannot_tell"
    finally:
        official_wording.dictionary.cache_clear()


def test_words_are_compared_after_undoing_the_readers_confusions() -> None:
    assert official_wording.capitals("Délégation n° 1") == "DELEGATIONN1"
    assert official_wording.fold("DELEGATLON") == official_wording.fold("DELEGATION") == official_wording.fold("DE1EGAT10N")
    assert official_wording.wrong_letters("IMMATRICULATION", "IMMATRICURATION") == 1
    assert official_wording.wrong_letters("DELEGATION", "DELEGATIN") == 1 and official_wording.wrong_letters("ABC", "ABC") == 0


def test_a_word_of_a_language_is_never_a_damaged_official_word() -> None:
    pytest.importorskip("spellchecker")
    known = official_wording.dictionary(("fr", "en", "es"))
    for word in ("IDENTIFICATION", "CERTIFICADO",  # a word of French, a word of Spanish
                 "NATIONALTY",  # "nationality" with a letter missing
                 "CODENATIONAL"):  # two words glued together
        assert official_wording.another_word(word, known), word
    for word in ("MNSBICULATION", "XQZWKJHGFDP"):
        assert not official_wording.another_word(word, known), word


def test_fallback_names_and_their_variants() -> None:
    families = ("LiberationSans", "LiberationSerif")
    for name in ("LiberationSans", "LiberationSans-Bold", "Liberation Sans Regular", "LIBERATIONSERIF-Italic"):
        assert is_fallback(name, families)
    for name in ("Arial", "OCRB", "Liberty", "Helvetica-Liberation"):
        assert not is_fallback(name, families)
