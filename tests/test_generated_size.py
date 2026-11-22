"""generated_size: the picture's own size recorded by Ingest, the pixel budget of image generators, and the
answer file's genere_ia. Every picture and PDF here is drawn by the tests."""

from __future__ import annotations

import io

import pymupdf
import pytest
import tomllib

from conftest import REPO_ROOT, drawn_picture, make_catalogue, make_settings, save_picture
from fraudcheck.checks.base import CheckResult, DocumentContext, registered
from fraudcheck.checks.runner import check_document, document_context
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.decision.answer import AnswerFileError, build_answer, validate_answer
from fraudcheck.decision.decide import DecisionConfig, decide_one
from fraudcheck.ingest.models import DocumentRecord, Page
from fraudcheck.ingest.pipeline import load_record, run_ingest

CONFIG = DecisionConfig(trusted=["font", "generated_size"], confidence={"font": 0.9, "generated_size": 0.9})


def picture_pdf(path, sizes) -> None:
    """A PDF page holding the given pictures and no text."""
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for position, size in enumerate(sizes):
        buffer = io.BytesIO()
        drawn_picture(size).save(buffer, format="PNG")
        top = 40 + position * 400
        page.insert_image(pymupdf.Rect(40, top, 555, top + 380), stream=buffer.getvalue())
    document.save(path)


@pytest.fixture
def ingested(workspace):
    settings = make_settings(workspace, max_side_px=3000)
    folder = settings.documents_path
    save_picture(drawn_picture((887, 1774)), folder / "DOC-1.png")  # 1,573,538 pixels: 0.04 % from 1024 x 1536
    save_picture(drawn_picture((1200, 1600)), folder / "DOC-2.png")  # a camera-like 4:3 picture
    save_picture(drawn_picture((3200, 2000)), folder / "DOC-3.png")  # needs a working copy: its own size is kept
    picture_pdf(folder / "DOC-4.pdf", [(1055, 1491)])  # one picture in a PDF page
    picture_pdf(folder / "DOC-5.pdf", [(1055, 1491), (300, 200)])  # two pictures: no answer
    save_picture(drawn_picture((1024, 1536)), folder / "DOC-6.png")  # exactly the budget
    make_catalogue(settings.catalogue_file, [(f"DOC-{n}", name, "Carte grise") for n, name in
                                             enumerate(["DOC-1.png", "DOC-2.png", "DOC-3.png", "DOC-4.pdf",
                                                        "DOC-5.pdf", "DOC-6.png"], start=1)])
    run_ingest(settings)
    return settings


def outcome(settings, doc_id: str) -> CheckResult | None:
    context = document_context(settings, CheckSettings(root=settings.root, doc_types_dir=REPO_ROOT / "config" / "doc_types"),
                               load_record(settings, doc_id))
    return check_document(context, {"generated_size": registered()["generated_size"]}).get("generated_size")


def test_ingest_keeps_each_pictures_own_size(ingested) -> None:
    assert load_record(ingested, "DOC-1").pages[0].pixel_size == (887, 1774)
    copy = load_record(ingested, "DOC-3").pages[0]
    assert copy.source == "working_copy" and (copy.width, copy.height) != (3200, 2000) and copy.pixel_size == (3200, 2000)
    drawn = load_record(ingested, "DOC-4").pages[0]
    assert drawn.source == "rendered" and drawn.pixel_size == (1055, 1491)  # the embedded picture, not the drawn page
    assert load_record(ingested, "DOC-5").pages[0].pixel_size is None


def test_a_free_shape_at_the_generators_pixel_budget_fails(ingested) -> None:
    result = outcome(ingested, "DOC-1")
    assert result.outcome == "fail" and result.ai_generated and result.fraud_type == "image générée ou retouchée par IA"
    assert "887 x 1774 pixels" in result.evidence and "1 572 864" in result.evidence
    assert outcome(ingested, "DOC-4").outcome == "fail"  # the single picture inside a PDF page
    assert outcome(ingested, "DOC-6").outcome == "fail"


def test_other_sizes_pass_and_unclear_pages_give_no_answer(ingested) -> None:
    assert outcome(ingested, "DOC-2").outcome == "pass" and outcome(ingested, "DOC-3").outcome == "pass"
    assert outcome(ingested, "DOC-5") is None  # a page with two pictures


def test_a_text_pdf_gives_no_answer() -> None:
    record = DocumentRecord(id="DOC-7", source_file="x.pdf", kind="pdf_text",
                            pages=[Page(number=1, picture="x.png", width=10, height=10, source="rendered",
                                        pixel_size=(1024, 1536))])
    context = DocumentContext(record=record, reading=None, text_layer=None, settings=CheckSettings(root=REPO_ROOT))
    assert not registered()["generated_size"].applies(context)


def test_budgets_and_tolerance_are_settings() -> None:
    settings = CheckSettings(root=REPO_ROOT, generated_pixel_budgets="1572864, 2359296", generated_size_tolerance=0.002)
    assert settings.generated_pixel_budgets == (1572864, 2359296) and settings.generated_size_tolerance == 0.002


def _fail(name: str, generated: bool) -> CheckResult:
    return CheckResult(check=name, outcome="fail", evidence="Constat sans valeur.", source="general",
                       clues=["indice"], ai_generated=generated)


def test_the_answer_file_says_genere_ia_only_for_a_generated_picture() -> None:
    record = lambda doc: DocumentRecord(id=doc, source_file="x.png")  # noqa: E731
    generated = decide_one("DOC-1", [record("DOC-1")], {"DOC-1": {"generated_size": _fail("generated_size", True)}},
                           [None], CONFIG)
    edited = decide_one("DOC-2", [record("DOC-2")], {"DOC-2": {"font": _fail("font", False)}}, [None], CONFIG)
    genuine = decide_one("DOC-3", [record("DOC-3")], {}, [None], CONFIG)
    assert generated.flagged and generated.generated and generated.confidence == 0.9
    assert edited.flagged and not edited.generated and not genuine.generated
    decisions = [generated, edited, genuine]
    text = build_answer(decisions, "team06", "intermediaire", None, [])
    data = validate_answer(text, decisions, "team06", "intermediaire")
    assert [item.get("genere_ia") for item in data["document"]] == [True, None, None]
    tampered = text.replace("genere_ia = true\n", "", 1)
    with pytest.raises(AnswerFileError, match="genere_ia"):
        validate_answer(tampered, decisions, "team06", "intermediaire")
    assert tomllib.loads(text)["document"][0]["frauduleux"] is True


def test_the_repository_trusts_generated_size() -> None:
    config = tomllib.loads((REPO_ROOT / "config" / "decision.toml").read_text(encoding="utf-8"))
    assert {"font", "generated_size", "covered_text"} <= set(config["trusted"])
    assert all(config["confidence"][name] == value
               for name, value in {"font": 0.9, "generated_size": 0.9, "covered_text": 0.85}.items())
    assert set(config["confidence"]) == set(config["trusted"])  # every trusted check has its confidence
