"""Steps 5 and 6: decision lines, answer file, evaluation, on generated documents and hand-made results."""

from __future__ import annotations

import tomllib

import pytest

from conftest import REPO_ROOT, STAND_IN_FALLBACK
from fraudcheck.checks.base import CheckResult
from fraudcheck.checks.runner import run_checks
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.cli import EXIT_OK, EXIT_SETUP_ERROR, main
from fraudcheck.decision.answer import AnswerFileError, answer_name, build_answer, toml_string, validate_answer, write_answer
from fraudcheck.decision.decide import (
    REVIEW_CONFIDENCE,
    Decision,
    DecisionConfig,
    DecisionSetupError,
    ReviewDecision,
    apply_review,
    decide,
    decide_one,
    leaks,
    load_decision_config,
    load_review_decisions,
    organiser_base,
    review_order,
)
from fraudcheck.decision.evaluate import evaluate, format_evaluation, load_marked
from fraudcheck.decision.settings import DecisionSettings
from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.models import DocumentRecord, Page
from fraudcheck.read.models import FieldValue, Reading, ReadingAnswer

DECISION_FILE = REPO_ROOT / "config" / "decision.toml"
CONFIG = DecisionConfig(trusted=["font"], confidence={"font": 0.9})


def record(doc_id: str, pages: int = 1) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, source_file=f"{doc_id}.png",
        pages=[Page(number=n, picture="p.png", width=10, height=10, source="original") for n in range(1, pages + 1)],
    )


def result(check: str, outcome: str, evidence: str = "Preuve du contrôle.", clues=("indice",)) -> CheckResult:
    return CheckResult(check=check, outcome=outcome, evidence=evidence, source="general", clues=list(clues))


def reading(doc_id: str, status="read", readable="yes", values=()) -> Reading:
    answer = ReadingAnswer(
        nature_seen="relevé", readable=readable, pages=[], elements=[],
        fields=[FieldValue(name=f"f{n}", value=value, state="read", other_copies=[]) for n, value in enumerate(values)],
    )
    return Reading(id=doc_id, status=status, answer=answer if status == "read" else None, model="terra")


def test_a_failed_trusted_check_flags_with_its_evidence() -> None:
    results = {"DOC-1": {"font": result("font", "fail", "Police de substitution."), "dates_order": result("dates_order", "fail")}}
    decision = decide_one("DOC-1", [record("DOC-1")], results, [None], CONFIG)
    assert decision.flagged and decision.line == "trusted_check_failed"
    assert decision.justification == "Police de substitution." and decision.confidence == 0.9
    assert decision.reported == ["dates_order"]


def test_the_most_trusted_failure_speaks_first() -> None:
    config = DecisionConfig(trusted=["covered_text", "font"], confidence={"covered_text": 0.85, "font": 0.9})
    covered = CheckResult(check="covered_text", outcome="fail", evidence="Texte masqué.", source="general",
                          clues=["texte masqué"], fraud_type="texte masqué puis réécrit")
    font = CheckResult(check="font", outcome="fail", evidence="Police de substitution.", source="general",
                       clues=["police"], fraud_type="texte modifié (police substituée)")
    decision = decide_one("DOC-1", [record("DOC-1")], {"DOC-1": {"covered_text": covered, "font": font}}, [None], config)
    assert decision.fraud_type == "texte modifié (police substituée)" and decision.confidence == 0.9
    assert decision.justification == "Police de substitution. Texte masqué."
    assert decision.clues == ["police", "texte masqué"] and decision.trusted_failed == ["covered_text", "font"]


def test_a_check_can_be_trusted_for_some_families_only(tmp_path) -> None:
    config = DecisionConfig(trusted=["typography_picture"], confidence={"typography_picture": 0.7},
                            families={"typography_picture": ["releve"]})
    failing = {"DOC-1": {"typography_picture": result("typography_picture", "fail", "Lettrage différent.")}}
    releve = record("DOC-1").model_copy(update={"family": "releve"})
    identity = record("DOC-1").model_copy(update={"family": "identity"})
    assert decide_one("DOC-1", [releve], failing, [None], config).flagged
    other = decide_one("DOC-1", [identity], failing, [None], config)
    assert not other.flagged and other.reported == ["typography_picture"] and other.review  # still shown to a person
    for bad in ('trusted = ["font"]\n[families]\ncovered_text = ["releve"]\n',  # not a trusted check
                'trusted = ["font"]\n[families]\nfont = ["passeport"]\n'):  # not a family
        path = tmp_path / "decision.toml"
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_decision_config(path)


def test_untrusted_failures_are_reported_but_never_flag() -> None:
    results = {"DOC-1": {"iban_check_digits": result("iban_check_digits", "fail"), "font": result("font", "pass")}}
    decision = decide_one("DOC-1", [record("DOC-1")], results, [None], CONFIG)
    assert not decision.flagged and decision.line == "not_flagged" and decision.reported == ["iban_check_digits"]


def test_unreadable_documents_go_to_the_review_list() -> None:
    empty = DocumentRecord(id="DOC-2", source_file="x.png")
    assert decide_one("DOC-2", [empty], {}, [None], CONFIG).line == "unreadable"
    no_reading = decide_one("DOC-3", [record("DOC-3")], {}, [reading("DOC-3", status="no_reading")], CONFIG)
    assert no_reading.line == "unreadable" and no_reading.review and not no_reading.flagged
    unreadable = decide_one("DOC-4", [record("DOC-4")], {}, [reading("DOC-4", readable="no")], CONFIG)
    assert unreadable.line == "unreadable"
    partly = decide_one("DOC-5", [record("DOC-5")], {}, [reading("DOC-5", readable="partly")], CONFIG)
    assert partly.line == "not_flagged"
    missing = decide_one("DOC-6", [], {}, [], CONFIG)
    assert missing.line == "unreadable" and not missing.flagged


def test_a_trusted_failure_wins_over_unreadability() -> None:
    results = {"DOC-7": {"font": result("font", "fail")}}
    flagged = decide_one("DOC-7", [record("DOC-7")], results, [reading("DOC-7", readable="no")], CONFIG)
    assert flagged.flagged and flagged.line == "trusted_check_failed"


def test_the_review_list_shows_untrusted_failures_with_evidence_most_failures_first() -> None:
    results = {
        "DOC-1": {"dates": result("dates", "fail", "Dates incohérentes."), "copies_agree": result("copies_agree", "fail", "Copies."),
                  "font": result("font", "pass")},
        "DOC-2": {"bonus_malus_range": result("bonus_malus_range", "fail", "Hors bornes.")},
        "DOC-3": {"dates": result("dates", "pass")},
    }
    decisions = [decide_one(doc, [record(doc)], results, [None], CONFIG) for doc in ("DOC-3", "DOC-2", "DOC-1")]
    decisions.append(decide_one("DOC-4", [DocumentRecord(id="DOC-4", source_file="x.png")], {}, [None], CONFIG))
    first = decisions[2]
    assert first.review and first.reported == ["copies_agree", "dates"] and not first.flagged
    assert "dates : Dates incohérentes." in first.review_reasons and "copies_agree : Copies." in first.review_reasons
    assert not decisions[0].review  # nothing failed: not listed
    assert [d.id for d in review_order(decisions)] == ["DOC-1", "DOC-2", "DOC-4"]


def test_duplicates_are_decided_together() -> None:
    results = {"DOC-9~2": {"font": result("font", "fail")}}
    decision = decide_one("DOC-9", [record("DOC-9"), record("DOC-9~2")], results, [None, None], CONFIG)
    assert decision.flagged and decision.documents == ["DOC-9", "DOC-9~2"]
    assert organiser_base("DOC-9~2") == "DOC-9"


def test_a_justification_quoting_a_read_value_is_replaced() -> None:
    assert leaks("Le titulaire est Jean Martin.", [reading("DOC-1", values=["Jean MARTIN"])])
    assert not leaks("Police de substitution.", [reading("DOC-1", values=["Jean MARTIN", "12"])])
    results = {"DOC-1": {"font": result("font", "fail", "Le nom Jean Martin est dans une autre police.")}}
    decision = decide_one("DOC-1", [record("DOC-1")], results, [reading("DOC-1", values=["Jean Martin"])], CONFIG)
    assert decision.flagged and "Martin" not in decision.justification


def test_decision_config_is_validated(tmp_path) -> None:
    path = tmp_path / "decision.toml"
    with pytest.raises(ConfigError, match="not found"):
        load_decision_config(path)
    path.write_text('trusted = ["no_such_check"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown"):
        load_decision_config(path)
    path.write_text('trusted = ["font"]\n[confidence]\nfont = 3\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="between 0 and 1"):
        load_decision_config(path)
    path.write_text('trusted = ["font"]\n', encoding="utf-8")
    assert load_decision_config(path).trusted == ["font"]


def test_answer_file_round_trips_through_tomllib() -> None:
    decisions = [
        Decision(id="DOC-1", flagged=False, line="not_flagged"),
        Decision(id="DOC-2", flagged=True, line="trusted_check_failed", confidence=1, fraud_type="texte modifié",
                 justification='Guillemets " et barre \\ et\nretour, accents éàç.', clues=["police « X »", "zone"]),
    ]
    text = build_answer(decisions, "Équipe 06", "final", "abc1234", ["gpt-6.1-terra"])
    data = validate_answer(text, decisions, "Équipe 06", "final")
    assert data["meta"] == {"equipe": "Équipe 06", "cas": "fraude", "etape": "final", "commit": "abc1234",
                            "modeles": ["gpt-6.1-terra"]}
    flagged = data["document"][1]
    assert flagged["justification"] == decisions[1].justification and flagged["confiance"] == 1.0
    assert "confiance" not in data["document"][0]
    assert tomllib.loads(toml_string("a\u0001b").join(["x = ", ""]))["x"] == "a\u0001b"
    with pytest.raises(AnswerFileError):
        validate_answer(text, decisions[:1], "Équipe 06", "final")
    with pytest.raises(AnswerFileError):
        validate_answer(text.replace("frauduleux = true", "frauduleux = false"), decisions, "Équipe 06", "final")
    assert answer_name("Data Corsaires") == "data_corsaires_fraude.toml"


def test_end_to_end_decision_and_answer_file(family_folder) -> None:
    with pytest.raises(DecisionSetupError):
        decide(family_folder, DecisionSettings(root=family_folder.root, decision_path=DECISION_FILE))  # no checks yet
    run_checks(family_folder, CheckSettings(root=family_folder.root, fallback_fonts=(STAND_IN_FALLBACK,)))
    settings = DecisionSettings(root=family_folder.root, decision_path=DECISION_FILE, commit="0000000", team="Test")
    run = decide(family_folder, settings)
    flagged = {decision.id for decision in run.decisions if decision.flagged}
    assert flagged == {"DOC-202"}  # one line set in the stand-in fallback font
    assert len(run.decisions) == 5 and run.review_path.is_file()
    review = run.review_path.read_text(encoding="utf-8-sig")
    assert review.splitlines()[0] == "id;flagged;unreadable;failed_checks;checks;reasons"
    answer = write_answer(family_folder, settings, run.decisions, run.models)
    data = tomllib.loads(answer.path.read_text(encoding="utf-8"))
    assert answer.path.name == "test_fraude.toml" and data["meta"]["commit"] == "0000000"
    entry = next(item for item in data["document"] if item["id"] == "DOC-202")
    assert entry["frauduleux"] and entry["indices"] and "Courier" in entry["justification"]
    assert "DOC-" not in entry["justification"]  # each document is judged on its own


def test_review_decisions_are_read_strictly(tmp_path) -> None:
    path = tmp_path / "review_decisions.csv"
    assert load_review_decisions(path) == {}  # no file: nobody decided anything
    path.write_text("id;decision;raison;ia\n# commentaire\n\nDOC-1;faux;Libellé mal orthographié.;oui\n"
                    "DOC-2;vrai;;\nDOC-3;FAUX;Champ retouché.\n", encoding="utf-8")
    reviews = load_review_decisions(path)
    assert list(reviews) == ["DOC-1", "DOC-2", "DOC-3"]
    assert reviews["DOC-1"].fake and reviews["DOC-1"].generated and reviews["DOC-1"].reason == "Libellé mal orthographié."
    assert not reviews["DOC-2"].fake and reviews["DOC-3"].fake and not reviews["DOC-3"].generated
    path.write_text("DOC-1,faux,Champ retouché\n", encoding="utf-8")  # commas work too
    assert load_review_decisions(path)["DOC-1"].fake
    for bad in ("DOC-1;peut-être;Champ retouché.\n",  # a decision must never be guessed
                "DOC-1;faux;\n", "DOC-1\n"):  # a document judged fake needs its reason
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigError, match="line 1"):
            load_review_decisions(path)


def test_a_persons_decision_replaces_the_automatic_one() -> None:
    results = {"DOC-1": {"font": result("font", "fail", "Police de substitution.")}}
    automatic = decide_one("DOC-1", [record("DOC-1")], results, [None], CONFIG)
    cleared = apply_review(automatic, ReviewDecision(id="DOC-1", fake=False), [None])
    assert not cleared.flagged and cleared.line == "reviewed" and cleared.reviewed and cleared.justification is None
    assert cleared.trusted_failed == ["font"]  # what the checks found stays on the record
    kept = apply_review(automatic, ReviewDecision(id="DOC-1", fake=True, reason="Champ retouché."), [None])
    assert kept.flagged and kept.justification == "Champ retouché. Police de substitution." and kept.confidence == 0.9
    quiet = decide_one("DOC-2", [record("DOC-2")], {}, [None], CONFIG)
    seen = apply_review(quiet, ReviewDecision(id="DOC-2", fake=True, reason="Libellé mal orthographié.", generated=True), [None])
    assert seen.flagged and seen.generated and seen.confidence == REVIEW_CONFIDENCE and seen.line == "reviewed"
    assert seen.clues == ["document examiné par une personne"] and seen.fraud_type == "document généré par IA"
    # a reason that copies a value read on the document never reaches the answer file
    leaking = ReviewDecision(id="DOC-2", fake=True, reason="Le titulaire est Jean Martin.")
    replaced = apply_review(quiet, leaking, [reading("DOC-2", values=["Jean MARTIN"])])
    assert replaced.flagged and "Martin" not in replaced.justification


def test_the_review_file_is_applied_by_the_decision_and_shown_in_the_answer(family_folder, caplog) -> None:
    run_checks(family_folder, CheckSettings(root=family_folder.root, fallback_fonts=(STAND_IN_FALLBACK,)))
    reviews = family_folder.root / "data" / "review_decisions.csv"
    reviews.write_text("DOC-201;faux;Champ retouché, vu à l'examen de l'image.\nDOC-202;vrai\n"
                       "DOC-999;faux;Document d'un autre jeu.\n", encoding="utf-8")
    settings = DecisionSettings(root=family_folder.root, decision_path=DECISION_FILE, commit="0000000", team="Test")
    with caplog.at_level("WARNING"):
        run = decide(family_folder, settings)
    by_id = {decision.id: decision for decision in run.decisions}
    assert {doc for doc, decision in by_id.items() if decision.flagged} == {"DOC-201"}
    assert by_id["DOC-202"].line == "reviewed" and by_id["DOC-202"].trusted_failed == ["font"]  # cleared by the person
    assert "DOC-999" in caplog.text  # a line naming no document of this set is said, never an error
    answer = write_answer(family_folder, settings, run.decisions, run.models)
    entry = next(item for item in tomllib.loads(answer.path.read_text(encoding="utf-8"))["document"] if item["id"] == "DOC-201")
    assert entry["frauduleux"] and entry["justification"].startswith("Champ retouché") and entry["confiance"] == 0.9


def test_marked_examples_are_read_in_several_forms(tmp_path) -> None:
    path = tmp_path / "marked.csv"
    assert load_marked(path).problems and not load_marked(path).labels
    path.write_text("", encoding="utf-8")
    assert "empty" in load_marked(path).problems[0]
    path.write_text("id;label\nDOC-1;fake\ndoc_2;Genuine\nDOC-3;faux\nDOC-4;maybe\n", encoding="utf-8")
    marked = load_marked(path)
    assert marked.labels == {"DOC-1": True, "DOC-2": False, "DOC-3": True} and "unknown label" in marked.problems[0]
    path.write_text("DOC-5,vrai\nDOC-6,fake\n", encoding="utf-8")
    assert load_marked(path).labels == {"DOC-5": False, "DOC-6": True}


def test_score_follows_the_organisers_rule() -> None:
    marked = load_marked.__globals__["MarkedExamples"](labels={f"DOC-{n}": n <= 6 for n in range(1, 11)})
    decisions = [Decision(id=f"DOC-{n}", flagged=n in (1, 2, 3, 4, 5, 7, 8, 9), line="not_flagged") for n in range(1, 11)]
    results = {"DOC-1": {"font": result("font", "fail")}, "DOC-7": {"font": result("font", "fail")}}
    report = evaluate(decisions, results, marked, evaluated=68)
    assert report.caught == ["DOC-1", "DOC-2", "DOC-3", "DOC-4", "DOC-5"] and report.wrong == ["DOC-7", "DOC-8", "DOC-9"]
    assert report.free_wrong_flags == 2
    assert report.penalty == pytest.approx(2 * (100 * 3 / 68 - 3))
    assert report.score == pytest.approx(5 - report.penalty)
    font = next(hit for hit in report.checks if hit.name == "font")
    assert (font.fails_on_fakes, font.fails_on_genuine, font.fails_overall) == (1, 1, 2)
    assert "Score for 68 evaluated documents" in format_evaluation(report)


def test_decide_and_evaluate_commands(family_folder, capsys, monkeypatch) -> None:
    monkeypatch.chdir(family_folder.root)
    for name, value in {"ROOT": family_folder.root, "DOCUMENTS_DIR": "data/documents", "CATALOGUE_PATH": "data/catalogue.xlsx"}.items():
        monkeypatch.setenv(f"INGEST_{name}", str(value))
    monkeypatch.setenv("CHECK_FALLBACK_FONTS", STAND_IN_FALLBACK)
    cache = ["--cache", str(family_folder.cache_path)]
    assert main(["decide", *cache]) == EXIT_SETUP_ERROR  # no check results yet
    assert main(["check", *cache]) == EXIT_OK
    assert main(["decide", *cache, "--team", "Test", "--commit", "1234567", "-v"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "1 flagged (DOC-202)" in out and "test_fraude.toml" in out and "decided by a person 0" in out
    marked = family_folder.root / "marked.csv"
    marked.write_text("id,label\nDOC-202,fake\nDOC-201,genuine\n", encoding="utf-8")
    assert main(["evaluate", *cache, "--marked", str(marked), "--evaluated", "5"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "fakes caught   1 (DOC-202)" in out and "wrong flags    0" in out


def test_run_command_does_every_stage(family_folder, capsys, monkeypatch) -> None:
    monkeypatch.chdir(family_folder.root)
    for name, value in {"ROOT": family_folder.root, "DOCUMENTS_DIR": "data/documents", "CATALOGUE_PATH": "data/catalogue.xlsx",
                        "FAMILIES_PATH": REPO_ROOT / "config" / "families.toml"}.items():
        monkeypatch.setenv(f"INGEST_{name}", str(value))
    monkeypatch.setenv("CHECK_FALLBACK_FONTS", STAND_IN_FALLBACK)
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_MODEL", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert main(["run", "--cache", str(family_folder.cache_path), "--team", "Test", "--commit", "1234567"]) == EXIT_OK
    out = capsys.readouterr().out
    for stage in ("Ingest: 5 documents", "Reading skipped", "Checks: 5 documents", "1 flagged (DOC-202)", "Flagged: 1"):
        assert stage in out
    assert main(["run", "--cache", str(family_folder.cache_path), "--read", "--read-limit", "1"]) == EXIT_OK
    assert "Azure is not configured" in capsys.readouterr().err
