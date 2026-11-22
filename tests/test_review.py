"""A person's decisions: the review file written line by line, and the reviewer screen driven as a user would."""

from __future__ import annotations

import pytest

from conftest import REPO_ROOT, STAND_IN_FALLBACK
from fraudcheck.checks.runner import run_checks
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.decision.decide import ReviewDecision, decide, load_decisions, load_review_decisions
from fraudcheck.decision.review import HEADER, remove_review_decision, save_review_decision
from fraudcheck.decision.settings import DecisionSettings

DECISION_FILE = REPO_ROOT / "config" / "decision.toml"
APP = REPO_ROOT / "src" / "fraudcheck" / "reviewer_app.py"


def test_a_decision_is_written_replaced_and_removed_line_by_line(tmp_path) -> None:
    path = tmp_path / "data" / "review_decisions.csv"
    save_review_decision(path, ReviewDecision(id="DOC-2", fake=True, reason="Champ retouché ; fond rapporté, police différente."))
    save_review_decision(path, ReviewDecision(id="DOC-10", fake=False))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER and lines[1].startswith("#") and len(lines) == 5
    reviews = load_review_decisions(path)
    assert reviews["DOC-2"].reason == "Champ retouché ; fond rapporté, police différente."  # ";" and "," survive
    assert not reviews["DOC-10"].fake
    # a second decision on the same document replaces the first, and never touches a document whose id starts alike
    save_review_decision(path, ReviewDecision(id="DOC-1", fake=True, reason="Libellé\nmal orthographié.", generated=True))
    save_review_decision(path, ReviewDecision(id="DOC-2", fake=False))
    reviews = load_review_decisions(path)
    assert list(reviews) == ["DOC-2", "DOC-10", "DOC-1"] and not reviews["DOC-2"].fake
    assert reviews["DOC-1"].generated and reviews["DOC-1"].reason == "Libellé mal orthographié."  # kept on one line
    assert remove_review_decision(path, "DOC-1") and not remove_review_decision(path, "DOC-1")
    assert list(load_review_decisions(path)) == ["DOC-2", "DOC-10"]
    with pytest.raises(ValueError, match="reason"):
        save_review_decision(path, ReviewDecision(id="DOC-3", fake=True, reason="  "))


def test_a_reason_with_more_commas_than_semicolons_is_still_read(tmp_path) -> None:
    path = tmp_path / "review_decisions.csv"
    path.write_text("DOC-1;faux;Police, netteté, fond, alignement, couleur\n", encoding="utf-8")
    assert load_review_decisions(path)["DOC-1"].reason == "Police, netteté, fond, alignement, couleur"


@pytest.fixture
def decided(family_folder, monkeypatch):
    """The five generated documents, checked and decided, with the screen's settings pointing at them."""
    run_checks(family_folder, CheckSettings(root=family_folder.root, fallback_fonts=(STAND_IN_FALLBACK,)))
    reviews = family_folder.root / "data" / "review_decisions.csv"
    decide(family_folder, DecisionSettings(root=family_folder.root, decision_path=DECISION_FILE,
                                           review_decisions_path=reviews, commit="0000000"))
    for name, value in {"INGEST_ROOT": family_folder.root, "INGEST_DOCUMENTS_DIR": "data/documents",
                        "INGEST_CATALOGUE_PATH": "data/catalogue.xlsx", "DECIDE_ROOT": family_folder.root,
                        "DECIDE_DECISION_PATH": DECISION_FILE, "DECIDE_REVIEW_DECISIONS_PATH": reviews,
                        "DECIDE_COMMIT": "0000000"}.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.chdir(family_folder.root)
    return family_folder, reviews


def test_the_reviewer_screen_shows_the_reasons_and_records_the_persons_decision(decided) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    folder, reviews = decided
    screen = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not screen.exception
    assert [metric.value for metric in screen.metric] == ["5", "1", "0", "0"]  # documents, flagged, by a person, to review
    assert screen.selectbox(key="document").value == "DOC-202"  # the flagged document comes first
    shown = " ".join(element.value for element in [*screen.markdown, *screen.error])
    assert "Signalé comme faux" in shown and "`font` (de confiance)" in shown and "Courier" in shown
    # the person judges it genuine: the flag goes, the answer file is rewritten
    screen.radio(key="verdict-DOC-202").set_value("Authentique")
    screen.button(key="save-DOC-202").click().run()
    assert not screen.exception and not load_review_decisions(reviews)["DOC-202"].fake
    assert not any(decision.flagged for decision in load_decisions(folder))
    assert (folder.cache_path / "answer" / "team06_fraude.toml").is_file()
    # a fake needs its reason; with one, the document is flagged by the person
    screen.radio(key="scope").set_value("Tous les documents").run()
    screen.selectbox(key="document").select("DOC-201").run()
    screen.radio(key="verdict-DOC-201").set_value("Faux")
    screen.button(key="save-DOC-201").click().run()
    assert "demande un motif" in " ".join(element.value for element in screen.error)
    screen.text_area(key="reason-DOC-201").input("Champ retouché, vu à l'examen de l'image.")
    screen.button(key="save-DOC-201").click().run()
    by_id = {decision.id: decision for decision in load_decisions(folder)}
    assert by_id["DOC-201"].flagged and by_id["DOC-201"].reviewed and by_id["DOC-201"].line == "reviewed"
    # and the person can step back: the automatic decision applies again
    screen.radio(key="verdict-DOC-201").set_value("Aucune : garder la décision automatique")
    screen.button(key="save-DOC-201").click().run()
    assert "DOC-201" not in load_review_decisions(reviews)
    assert not {decision.id: decision for decision in load_decisions(folder)}["DOC-201"].flagged


def test_the_reviewer_screen_says_what_to_run_when_nothing_is_decided(workspace, monkeypatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("INGEST_ROOT", str(workspace))
    monkeypatch.setenv("DECIDE_ROOT", str(workspace))
    monkeypatch.chdir(workspace)
    screen = testing.AppTest.from_file(str(APP), default_timeout=60).run()
    assert not screen.exception and "python -m fraudcheck run" in screen.warning[0].value
