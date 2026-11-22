"""Step 4: checks by family on the reading, with hand-made readings only."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import REPO_ROOT, drawn_picture, make_catalogue, make_settings, save_picture
from fraudcheck.checks.base import DocumentContext, registered
from fraudcheck.checks.family._values import parse_date, parse_number, same_value
from fraudcheck.checks.family.releve import bonus_coefficient
from fraudcheck.checks.family.rib import iban_valid, rib_key_valid
from fraudcheck.checks.runner import run_checks
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.decision.decide import decide
from fraudcheck.decision.settings import DecisionSettings
from fraudcheck.ingest.models import DocumentRecord, Page
from fraudcheck.ingest.pipeline import run_ingest
from fraudcheck.read.models import ElementCheck, FieldValue, Reading, ReadingAnswer
from fraudcheck.read.store import save_reading

DOC_TYPES = REPO_ROOT / "config" / "doc_types"
TODAY = date(2026, 11, 21)
NATURES = {
    "releve": ("releve", "Relevé d'information (relevé de sinistres)"),
    "rib": ("rib", "Relevé d'identité bancaire"),
    "carte_grise": ("carte_grise", "Carte grise"),
    "permis_nouveau_verso": ("licence", "Permis de conduire nouveau modèle, verso"),
    "permis_nouveau_recto": ("licence", "Permis de conduire nouveau modèle, recto"),
    "permis_ancien_recto": ("licence", "Permis de conduire ancien modèle, recto"),
    "titre_sejour_verso": ("identity", "Titre de séjour, verso"),
    "permis_etranger": ("licence", "Permis de conduire étranger, recto"),
    "titre_sejour_recto": ("identity", "Titre de séjour, recto"),
    "piece_identite": ("identity", "Pièce d'identité"),
    "attestation_asile": ("identity", "Attestation de demande d'asile"),
    "attestation_prolongation": ("identity", "Attestation de prolongation d'instruction d'un titre de séjour"),
}


def rib_parts(bank="30004", branch="00823", account="00012345678") -> tuple[str, str]:
    """A made-up but valid French RIB key and IBAN for these codes."""
    digits = "".join({"A": "1"}.get(ch, ch) for ch in account)
    key = f"{97 - (89 * int(bank) + 15 * int(branch) + 3 * int(digits)) % 97:02d}"
    bban = bank + branch + account + key
    check = 98 - int("".join(str(int(ch, 36)) for ch in bban + "FR00")) % 97
    return key, f"FR{check:02d}{bban}"


KEY, IBAN = rib_parts()


def field(name, value=None, state=None, copies=()) -> FieldValue:
    return FieldValue(name=name, value=value, state=state or ("read" if value else "absent"), other_copies=list(copies))


def context(doc_type: str, fields: list[FieldValue], readable: str = "yes", status: str = "read",
            elements: dict[str, str] | None = None) -> DocumentContext:
    family, nature = NATURES[doc_type]
    record = DocumentRecord(id="DOC-1", source_file="x.png", nature=nature, family=family, kind="picture",
                            pages=[Page(number=1, picture="x.png", width=10, height=10, source="original")])
    seen = [ElementCheck(name=name, state=state) for name, state in (elements or {}).items()]
    answer = ReadingAnswer(nature_seen=nature, readable=readable, pages=[], fields=fields, elements=seen)
    reading = Reading(id="DOC-1", status=status, answer=answer if status == "read" else None, doc_type=doc_type)
    settings = CheckSettings(root=REPO_ROOT, doc_types_dir=DOC_TYPES, reference_date=TODAY)
    return DocumentContext(record=record, reading=reading, text_layer=None, settings=settings)


def outcome(check: str, ctx: DocumentContext) -> str | None:
    instance = registered()[check]
    if not instance.applies(ctx):
        return None
    result = instance.run(ctx)
    for item in ctx.reading.answer.fields:  # evidence never quotes a value read on the document
        for text in [item.value, *item.other_copies]:
            if text and len(text) >= 4:
                assert text not in result.evidence, (check, text)
    return result.outcome


@pytest.mark.parametrize(
    ("text", "expected", "impossible"),
    [
        ("12 07 1996", date(1996, 7, 12), False), ("12.07.96", date(1996, 7, 12), False),
        ("03.06.17", date(2017, 6, 3), False), ("1996-07-12", date(1996, 7, 12), False),
        ("12 juillet 1996", date(1996, 7, 12), False), ("1er mars 2020", date(2020, 3, 1), False),
        ("31.02.2019", None, True), ("13/13/2020", None, True), ("juil. 2019", None, False), ("", None, False),
    ],
)
def test_printed_dates_are_understood(text, expected, impossible) -> None:
    parsed = parse_date(text, 2026)
    assert parsed.value == expected and parsed.impossible is impossible


def test_numbers_and_bonus_malus_forms() -> None:
    assert parse_number("124,7 cm3") == 124.7 and parse_number("11 kW") == 11 and parse_number("abc") is None
    assert bonus_coefficient("0,68") == 0.68 and bonus_coefficient("50 %") == 0.5
    assert bonus_coefficient("Bonus 50 %") == 0.5 and bonus_coefficient("Malus 25%") == 1.25
    assert bonus_coefficient("100") == 1.0 and bonus_coefficient("CRM : 0.85") == 0.85
    # forms seen in the wild: the coefficient with its percentage, a labelled percentage, a date beside it
    assert bonus_coefficient("0,50 (50 %)") == 0.5 and bonus_coefficient("Bonus-malus : 90 %") == 0.9
    assert bonus_coefficient("au 15/03/2024 : 0,64") == 0.64 and bonus_coefficient("Réduction 5 %") == 0.95
    assert parse_number("1 198 cm3") == 1198 and parse_number("P.1 125") == 125


RELEVE_OK = [field("date_edition", "01/09/2026"), field("date_souscription", "15/03/2019"),
             field("situation_contrat", "En cours"), field("bonus_malus", "0,50"), field("vehicule", "Moto 125")]


def test_releve_items_follow_the_fiche() -> None:
    assert outcome("releve_items", context("releve", RELEVE_OK)) == "pass"
    # mopeds and light motorcycles are outside the bonus-malus clause: their relevé may print no coefficient
    no_bonus = [f for f in RELEVE_OK if f.name != "bonus_malus"] + [field("bonus_malus")]
    assert outcome("releve_items", context("releve", no_bonus)) == "pass"
    no_date = [f for f in RELEVE_OK if f.name != "date_edition"] + [field("date_edition")]
    assert outcome("releve_items", context("releve", no_date)) == "fail"
    no_status = [f for f in RELEVE_OK if f.name != "situation_contrat"] + [field("situation_contrat")]
    assert outcome("releve_items", context("releve", no_status)) == "pass"  # the fiche allows it
    blurry = [f for f in RELEVE_OK if f.name != "date_edition"] + [field("date_edition", state="unreadable")]
    assert outcome("releve_items", context("releve", blurry)) == "cannot_tell"
    plate_only = [f for f in RELEVE_OK if f.name != "vehicule"] + [field("vehicule"), field("immatriculation", "AB-123-CD")]
    assert outcome("releve_items", context("releve", plate_only)) == "pass"


@pytest.mark.parametrize(("printed", "expected"), [("0,45", "fail"), ("1,00", "pass"), ("3,6", "fail"), ("bonus 50 %", "pass")])
def test_bonus_malus_bounds(printed, expected) -> None:
    assert outcome("bonus_malus_range", context("releve", [field("bonus_malus", printed)])) == expected


def test_dates_exist_and_come_in_order() -> None:
    assert outcome("dates", context("releve", RELEVE_OK)) == "pass"
    late = [field("date_edition", "01/09/2018"), field("date_souscription", "15/03/2019")]
    assert outcome("dates", context("releve", late)) == "fail"
    impossible = [field("date_edition", "31/02/2026")]
    assert outcome("dates", context("releve", impossible)) == "fail"
    one_separator_too_many = [field("date_edition", "09./09/2026"), field("date_souscription", "15/03/2019")]
    assert outcome("dates", context("releve", one_separator_too_many)) == "fail"  # seen on a real retyped relevé
    assert outcome("dates", context("releve", [field("date_edition", "09.09.2026")])) == "pass"
    # the dashes between the two dates of a period are not a separator too many
    assert outcome("dates", context("releve", [field("date_edition", "15/03/2019 -- 14/03/2020")])) == "pass"
    # an end of cover with a two-digit year lies in this century: "27" is 2027, after a subscription in 2019
    future = [field("date_souscription", "15/03/19"), field("date_resiliation", "15/01/27")]
    assert outcome("dates", context("releve", future)) == "pass"
    assert parse_date("12.07.0000", 2026) == parse_date("", 2026)  # a placeholder is not a date, and does not crash
    assert outcome("dates", context("releve", [field("date_edition", state="unreadable")])) == "cannot_tell"


def test_copies_agree_after_normalising_spaces_case_and_accents() -> None:
    same = [field("immatriculation", "AB-123-CD", copies=["ab-123-cd ", "AB-123-CD"])]
    assert outcome("copies_agree", context("carte_grise", same)) == "pass"
    # free text (a holder, a company, a list of drivers) is not compared: it is written differently from place to place
    titles = [field("titulaire", "Exemple Assurance", copies=["EXEMPLE", "Exemple Courtier habilité"])]
    assert outcome("copies_agree", context("carte_grise", titles)) == "cannot_tell"
    differs = [field("immatriculation", "AB-123-CD", copies=["AB-128-CD"])]
    assert outcome("copies_agree", context("carte_grise", differs)) == "fail"
    assert outcome("copies_agree", context("carte_grise", [field("immatriculation", "AB-123-CD")])) is None


@pytest.mark.parametrize(
    ("printed", "copy", "same"),
    [
        ("AB-123-CD", "AB123CD", True),  # the plate without hyphens in the machine-readable line
        ("AB-123-CD", "AB123CD9", True),  # the same, copied with its check digit
        ("06/01/2016", "160106", True),  # a date as YYMMDD in the machine-readable line
        ("06 01 2016", "060116", True), ("06/01/2016", "06.01.2016", True), ("6 janvier 2016", "06/01/16", True),
        ("Alice, Marie, Zoe", "ALICE<MARIE<ZOE", True),  # first names in a machine-readable line
        ("Alice Marie Zoe", "ALICE", True),  # cut short
        ("Exempleville (075)", "EXEMPLEVILLE", True),  # a birthplace with and without its department number
        ("EXEMPLE\nAlice", "Alice EXEMPLE", True),  # two lines, or the other order
        ("06/01/2016", "160107", False), ("06/01/2016", "07/01/2016", False),
        ("AB-123-CD", "AB128CD", False), ("EXEMPLE", "EXAMPLE", False), ("Alice Marie", "ALICE<ZOE", False),
        ("01/03/2015", "1503019", True),  # the date with its check digit, as a machine-readable line prints it
        ("ZZZTEST0123456789", "ZZZTESTO123456789", True),  # O read for 0 in one of the two copies
        ("FR76 3000 4008 2300 0123 4567 845", "30004 00823 00012345678 45", True),  # the IBAN and its parts
    ],
)
def test_two_printed_forms_of_one_value_are_the_same_value(printed, copy, same) -> None:
    assert same_value(printed, copy, 2026) is same and same_value(copy, printed, 2026) is same
    fields = [field("immatriculation", printed, copies=[copy])]
    assert outcome("copies_agree", context("carte_grise", fields)) == ("pass" if same else "fail")


def test_iban_check_digits_with_misread_repair() -> None:
    assert iban_valid(IBAN) and rib_key_valid("30004", "00823", "00012345678", KEY)
    assert outcome("iban_check_digits", context("rib", [field("iban", " ".join(IBAN[i:i + 4] for i in range(0, 27, 4)))])) == "pass"
    wrong = IBAN[:10] + str((int(IBAN[10]) + 1) % 10) + IBAN[11:]
    assert outcome("iban_check_digits", context("rib", [field("iban", wrong)])) == "fail"
    misread = IBAN[:12] + IBAN[12:].replace("0", "O", 1)
    assert outcome("iban_check_digits", context("rib", [field("iban", misread)])) == "pass"
    assert outcome("iban_check_digits", context("rib", [field("iban", state="unreadable")])) == "cannot_tell"


def test_rib_key_and_iban_against_its_parts() -> None:
    parts = [field("code_banque", "30004"), field("code_guichet", "00823"), field("numero_compte", "00012345678"),
             field("cle_rib", KEY), field("iban", IBAN)]
    assert outcome("rib_key", context("rib", parts)) == "pass"
    assert outcome("iban_parts", context("rib", parts)) == "pass"
    bad_key = parts[:3] + [field("cle_rib", f"{(int(KEY) + 1) % 97:02d}"), field("iban", IBAN)]
    assert outcome("rib_key", context("rib", bad_key)) == "fail"
    assert outcome("iban_parts", context("rib", bad_key)) == "fail"
    other_branch = [parts[0], field("code_guichet", "00824"), *parts[2:]]
    assert outcome("iban_parts", context("rib", other_branch)) == "fail"
    misread_branch = [parts[0], field("code_guichet", "0O823"), *parts[2:]]  # one O read for a 0
    assert outcome("iban_parts", context("rib", misread_branch)) == "pass"
    assert outcome("rib_key", context("rib", [field("iban", IBAN)])) == "pass"  # parts taken from the IBAN


@pytest.mark.parametrize(
    ("genre", "cm3", "kw", "expected"),
    [("MTL", "125", "11", "pass"), ("MTL", "600", "11", "fail"), ("MTT1", "650", "35", "fail"), ("MTT 1", "650", "25", "pass"),
     ("CL", "49", "3", "pass"), ("CL", "125", "8", "fail"), ("MTT2", "900", "70", None), ("MTL", None, "9", "cannot_tell")],
)
def test_carte_grise_category_against_engine(genre, cm3, kw, expected) -> None:
    fields = [field("genre", genre), field("cylindree", cm3, state=None if cm3 else "unreadable"), field("puissance_kw", kw)]
    assert outcome("cg_category_power", context("carte_grise", fields)) == expected


@pytest.mark.parametrize(
    ("a", "a2", "birth", "expected"),
    [
        ("01/05/2018", "01/01/2015", None, "pass"),
        ("01/05/2018", "01/01/2017", None, "fail"),
        ("01/05/2018", "", None, "fail"),
        ("01/05/2014", "", None, "cannot_tell"),
        # with a birth date the type is the foreign licence: French category rules do not judge it (direct
        # access to A at 24 is the European rule, and APRIL's sheet accepts an A obtained directly in the EU)
        ("01/05/2014", "", "01/01/1980", None),
        ("01/05/2014", "01/01/2013", "01/01/1995", None),
        ("01/05/2010", "", "01/01/1995", None),
        ("01/05/2010", "", None, "cannot_tell"),
    ],
)
def test_licence_category_a_against_its_date(a, a2, birth, expected) -> None:
    fields = [field("date_categorie_A", a), field("date_categorie_A2", a2 or None)]
    if birth:
        fields.append(field("date_naissance", birth))
    assert outcome("licence_categories", context("permis_etranger" if birth else "permis_nouveau_verso", fields)) == expected


def test_licence_without_category_a_is_not_judged() -> None:
    assert outcome("licence_categories", context("permis_nouveau_verso", [field("date_categorie_A")])) is None
    assert outcome("licence_categories", context("permis_nouveau_verso", [field("date_categorie_A", state="unreadable")])) == "cannot_tell"


def test_a_validity_date_already_past_fails_at_the_date_of_the_check() -> None:
    """TODAY is 21/11/2026: APRIL's sheets ask for a validity date still to come."""
    past = context("titre_sejour_recto", [field("date_expiration", "01/01/2025")])
    result = registered()["validity_expired"].run(past)
    assert result.outcome == "fail" and result.source == "fiche" and "21/11/2026" in result.evidence
    assert outcome("validity_expired", context("titre_sejour_recto", [field("date_expiration", "01/01/2031")])) == "pass"
    assert outcome("validity_expired", context("titre_sejour_recto", [field("date_expiration", "21/11/2026")])) == "pass"
    assert outcome("validity_expired", context("piece_identite", [field("date_expiration", state="unreadable")])) == "cannot_tell"
    # licences too: the renewal date of a card licence (4b), the validity date of a foreign one
    assert outcome("validity_expired", context("permis_nouveau_recto", [field("date_expiration", "15.03.2024")])) == "fail"
    assert outcome("validity_expired", context("permis_nouveau_recto", [field("date_expiration", "15.03.2034")])) == "pass"
    assert outcome("validity_expired", context("permis_etranger", [field("date_expiration", "28.02.28")])) == "pass"
    assert outcome("validity_expired", context("permis_nouveau_verso", [field("date_categorie_A", "01/05/2019")])) is None


def test_identity_cards_of_2004_to_2013_are_valid_five_years_longer_than_printed() -> None:
    def card(issued: str, ends: str, born: str = "15.01.1980") -> list:
        return [field("date_naissance", born), field("date_delivrance", issued), field("date_expiration", ends)]

    assert outcome("validity_expired", context("piece_identite", card("10.03.2012", "09.03.2022"))) == "pass"  # until 2027
    assert outcome("validity_expired", context("piece_identite", card("10.03.2010", "09.03.2020"))) == "fail"  # 2025 is past
    assert outcome("validity_expired", context("piece_identite", card("10.03.2015", "09.03.2025"))) == "fail"  # outside the window
    minor = card("10.03.2012", "09.03.2022", born="15.01.2000")  # a child's card keeps its printed ten years
    assert outcome("validity_expired", context("piece_identite", minor)) == "fail"


@pytest.mark.parametrize(
    ("edited", "expected"),
    [("15/10/2026", "pass"), ("21/08/2026", "pass"), ("20/08/2026", "fail"), ("01/03/2025", "fail"),
     ("22/11/2026", "fail"),  # dated after the date of the check
     ("", "cannot_tell")],
)
def test_a_relevé_older_than_three_months_at_the_date_of_the_check_fails(edited, expected) -> None:
    fields = [field("date_edition", edited or None, state=None if edited else "unreadable")]
    assert outcome("releve_too_old", context("releve", fields)) == expected


def test_family_checks_need_a_usable_reading() -> None:
    unreadable = context("releve", RELEVE_OK, readable="no")
    assert all(outcome(name, unreadable) is None for name in ("releve_items", "dates", "bonus_malus_range"))
    assert outcome("iban_check_digits", context("releve", RELEVE_OK)) is None  # not a RIB


def test_readings_feed_the_table_and_never_flag(workspace) -> None:
    settings = make_settings(workspace)
    save_picture(drawn_picture(), settings.documents_path / "DOC-1.png")
    make_catalogue(settings.catalogue_file, [("DOC-1", "DOC-1.png", "Relevé d'information (relevé de sinistres)")])
    run_ingest(settings)
    bad = [f for f in RELEVE_OK if f.name != "bonus_malus"] + [field("bonus_malus", "0,30")]
    answer = ReadingAnswer(nature_seen="relevé", readable="yes", pages=[], fields=bad, elements=[])
    save_reading(settings, Reading(id="DOC-1", status="read", answer=answer, doc_type="releve", model="terra"))
    run = run_checks(settings, CheckSettings(root=workspace, doc_types_dir=DOC_TYPES, reference_date=TODAY))
    assert run.results["DOC-1"]["bonus_malus_range"].outcome == "fail"
    assert run.results["DOC-1"]["releve_items"].outcome == "pass"
    decision = decide(settings, DecisionSettings(root=workspace, decision_path=REPO_ROOT / "config" / "decision.toml"))
    [only] = decision.decisions
    assert not only.flagged and "bonus_malus_range" in only.reported and decision.models == ["terra"]
