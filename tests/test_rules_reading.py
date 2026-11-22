"""Step 4, rules on the reading: machine-readable lines, licence ages and card dates, plate and VIN, BIC,
the RIB fiche's refusals, the European relevé and the bonus-malus history. Hand-made readings only:
every value below is invented (names such as TESTEUR, numbers built by the tests).
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from fraudcheck.checks.base import registered
from fraudcheck.ingest.models import Barcode
from fraudcheck.checks.family._mrz import check_digit, parse
from fraudcheck.checks.family.carte_grise_lines import parse_lines
from fraudcheck.checks.family.identity_rules import PROLONGATION_TITLE
from fraudcheck.checks.family.releve_rules import SECTIONS
from fraudcheck.read.doc_types import doc_type_named, load_doc_types
from conftest import REPO_ROOT
from test_checks_family import DOC_TYPES, IBAN, context, field, outcome


def td1(number="AB1234567", birth="900115", expiry="300115", surname="TESTEUR", given="ESSAI<PREMIER") -> str:
    """Three lines of 30, check digits computed (a residence permit or an identity card since 2021)."""
    one = f"IDFRA{number}{check_digit(number)}" + "<" * 15
    two = f"{birth}{check_digit(birth)}M{expiry}{check_digit(expiry)}FRA" + "<" * 11
    two += str(check_digit(one[5:30] + two[0:7] + two[8:15] + two[18:29]))
    return "\n".join([one, two, f"{surname}<<{given}".ljust(30, "<")[:30]])


def td3(number="XY9876543", birth="851231", expiry="310630", surname="TESTEUR", given="ESSAI") -> str:
    """Two lines of 44 (a passport)."""
    one = f"P<FRA{surname}<<{given}".ljust(44, "<")
    personal = "<" * 14
    two = (f"{number}{check_digit(number)}FRA{birth}{check_digit(birth)}F{expiry}{check_digit(expiry)}"
           f"{personal}<")
    two += str(check_digit(two[0:10] + two[13:20] + two[21:43]))
    return one + "\n" + two


def cni_1995(number="123456789012", given="ESSAI<<PREMIER", birth="900115") -> str:
    """Two lines of 36 of the French identity card of 1995."""
    one = "IDFRA" + "TESTEUR".ljust(25, "<") + "075123"
    two = f"{number}{check_digit(number)}{given.ljust(14, '<')[:14]}{birth}{check_digit(birth)}M"
    return one + "\n" + two + str(check_digit(one + two))


IDENTITY = [field("nom", "Testeur"), field("prenoms", "Essai, Premier"), field("date_naissance", "15/01/1990"),
            field("date_expiration", "15/01/2030"), field("numero_document", "AB1234567")]


# --- machine-readable lines ------------------------------------------------------------------------------------


def test_check_digits_follow_the_icao_worked_example() -> None:
    assert check_digit("520727") == 3  # ICAO Doc 9303, part 3: the worked example of a date


def test_layouts_are_found_from_the_total_length() -> None:
    assert parse(td1()).layout == "TD1" and parse(td1().replace("\n", " ")).layout == "TD1"
    assert parse(td3()).layout == "TD3" and parse(cni_1995()).layout == "CNI 1995"
    assert parse("IDFRA<<<") is None and parse("pas des lignes de lecture") is None
    lines = parse(td1())
    assert (lines.number, lines.birth, lines.expiry, lines.surname, lines.given_names) == (
        "AB1234567", "900115", "300115", "TESTEUR", "ESSAI PREMIER")
    assert not parse(td3()).failed and not parse(cni_1995()).failed and not lines.failed


def test_wrong_check_digits_fail_but_a_usual_misread_passes() -> None:
    assert outcome("mrz_check_digits", context("piece_identite", [field("mrz", td1())])) == "pass"
    edited = td1().replace("900115", "900116", 1)  # a birth date changed without its check digit
    assert outcome("mrz_check_digits", context("piece_identite", [field("mrz", edited)])) == "fail"
    misread = td1(number="AB1234560").replace("AB1234560", "AB123456O", 1)
    assert outcome("mrz_check_digits", context("titre_sejour_verso", [field("mrz", misread)])) == "pass"
    assert outcome("mrz_check_digits", context("piece_identite", [field("mrz", "IDFRA<<<<")])) == "cannot_tell"
    assert outcome("mrz_check_digits", context("piece_identite", [field("mrz", state="absent")])) is None
    assert outcome("mrz_check_digits", context("piece_identite", [field("mrz", cni_1995())])) == "pass"


def test_the_lines_agree_with_the_printed_fields() -> None:
    assert outcome("mrz_matches_print", context("piece_identite", [*IDENTITY, field("mrz", td1())])) == "pass"
    accents = [field("nom", "Tèsteür"), *IDENTITY[1:]]
    assert outcome("mrz_matches_print", context("piece_identite", [*accents, field("mrz", td1())])) == "pass"
    cut = td1(given="ESSAI<PREM")  # long given names are cut short in the lines
    assert outcome("mrz_matches_print", context("piece_identite", [*IDENTITY, field("mrz", cut)])) == "pass"
    other_birth = td1(birth="910115")
    assert outcome("mrz_matches_print", context("piece_identite", [*IDENTITY, field("mrz", other_birth)])) == "fail"
    other_number = td1(number="AB7654321")
    assert outcome("mrz_matches_print", context("piece_identite", [*IDENTITY, field("mrz", other_number)])) == "fail"


def test_names_printed_the_wrong_way_round_are_reported() -> None:
    swapped = [field("nom", "Essai"), field("prenoms", "Testeur"), *IDENTITY[2:]]
    ctx = context("piece_identite", [*swapped, field("mrz", td1(given="ESSAI"))])
    assert outcome("mrz_matches_print", ctx) == "fail"
    assert "inversés" in registered()["mrz_matches_print"].run(ctx).evidence


def test_a_passport_and_the_old_card_are_compared_too() -> None:
    passport = [field("nom", "Testeur"), field("prenoms", "Essai"), field("date_naissance", "31/12/1985"),
                field("date_expiration", "30/06/2031"), field("numero_document", "XY9876543")]
    assert outcome("mrz_matches_print", context("piece_identite", [*passport, field("mrz", td3())])) == "pass"
    old = [field("nom", "Testeur"), field("prenoms", "Essai, Premier"), field("date_naissance", "15.01.1990"),
           field("numero_document", "1234 5678 9012")]
    assert outcome("mrz_matches_print", context("piece_identite", [*old, field("mrz", cni_1995())])) == "pass"


# --- licence ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ([field("date_naissance", "01/01/2000"), field("date_delivrance", "01/06/2015"), field("categories", "AM/A1/B")],
         "fail"),  # 15 at issue, while B needs 17
        ([field("date_naissance", "01/01/2000"), field("date_delivrance", "01/06/2018"), field("categories", "AM/A1/B")],
         "pass"),
        ([field("date_naissance", "01/01/2000"), field("date_categorie_A", "01/01/2019")], "fail"),  # A at 19 after 2013
        ([field("date_naissance", "01/01/1990"), field("date_categorie_A", "01/06/2010")], "pass"),  # A at 20 before 2013
        ([field("date_naissance", "01/01/2000"), field("categories", "B")], "cannot_tell"),
    ],
)
def test_categories_need_their_minimum_age(fields, expected) -> None:
    assert outcome("licence_min_age", context("permis_nouveau_recto", fields)) == expected


def test_an_old_licence_issued_before_fourteen_fails_and_foreign_licences_are_not_judged() -> None:
    young = [field("date_naissance", "01/01/1990"), field("date_delivrance", "01/01/2003")]
    assert outcome("licence_min_age", context("permis_ancien_recto", young)) == "fail"
    assert outcome("licence_min_age", context("permis_etranger", young)) is None


@pytest.mark.parametrize(
    ("issued", "ends", "expected"),
    [("01/05/2012", "01/05/2027", "fail"), ("10/01/2015", "10/01/2030", "pass"), ("10/01/2015", "10/01/2032", "fail"),
     ("10/01/2015", "10/01/2020", "pass"), ("10/01/2015", None, "pass"),
     ("10/01/2015", "10/01/2025", "fail"),  # ten years: neither the 15 years of a card nor a limited validity
     ("10/01/2015", "20/12/2029", "pass"),  # fifteen years counted three weeks short: inside the tolerance
     ("10/01/2015", "01/02/2020", "pass")],  # five years and three weeks: inside the tolerance too
)
def test_the_card_model_dates(issued, ends, expected) -> None:
    fields = [field("date_delivrance", issued), field("date_expiration", ends)]
    assert outcome("licence_card_dates", context("permis_nouveau_recto", fields)) == expected
    assert outcome("licence_card_dates", context("permis_ancien_recto", fields)) is None


@pytest.mark.parametrize(
    ("listed", "expected"),
    [("AM A1 A2 A B1 B", "pass"), ("AM/B1/B", "pass"), ("AM A1 A2", "pass"),  # no heavier category: nothing to include
     ("A B1 B", "fail"),  # A without the A2 it includes
     ("AM A2 A B", "fail"),  # B without B1
     ("A B", "fail"), ("***", "cannot_tell")],
)
def test_a_category_is_listed_on_the_front_with_those_it_includes(listed, expected) -> None:
    fields = [field("categories", listed)]
    assert outcome("licence_front_categories", context("permis_nouveau_recto", fields)) == expected
    assert outcome("licence_front_categories", context("permis_ancien_recto", fields)) is None  # the card model only
    assert outcome("licence_front_categories", context("permis_nouveau_recto", [field("categories")])) is None


@pytest.mark.parametrize(
    ("dates", "expected"),
    [({"AM": "20.11.2031", "A": "20.11.2031", "B": "20.11.2031"}, "pass"),
     ({"AM": "20.11.26", "A": "20.11.26", "B": "05.03.31"}, "fail"),  # three categories, two dates on one card
     ({"A2": "20/11/2031", "A": "20 11 2031"}, "pass"),  # the same date printed two ways
     ({"A": "20.11.2031"}, None),  # one date alone: nothing to compare
     ({}, None)],  # a French card usually leaves column 11 empty
)
def test_motorbike_and_car_categories_of_one_card_share_one_validity(dates, expected) -> None:
    fields = [field(f"fin_validite_{category}", printed) for category, printed in dates.items()]
    assert outcome("licence_category_validity", context("permis_etranger", fields)) == expected
    assert outcome("licence_category_validity", context("permis_nouveau_verso", fields)) == expected


def both_sides(front: str, dated: dict[str, str | None], unreadable: tuple[str, ...] = ()) -> list:
    """A card scanned on both sides: the front's list of categories, and the back's date for each row read."""
    fields = [field("categories", front)]
    for category in ("AM", "A1", "A2", "A", "B1", "B"):
        state = "unreadable" if category in unreadable else None
        fields.append(field(f"date_categorie_{category}", dated.get(category), state=state))
    return fields


def test_the_categories_listed_on_the_front_are_the_ones_dated_on_the_back() -> None:
    def judged(*args, **options):
        return outcome("licence_sides_agree", context("permis_nouveau_recto", both_sides(*args, **options)))

    same_day, earlier = "20.02.23", "25.07.22"
    held = {"AM": same_day, "A1": same_day, "A2": same_day, "B1": earlier, "B": earlier}
    assert judged("AM A1 A2 B1 B", held) == "pass"
    assert judged("AM B1 B", {"AM": earlier, "B1": earlier, "B": earlier}) == "pass"
    # the date sits in the row of A instead of A1: the front still lists A1 and not A
    moved = {**held, "A1": None, "A": same_day}
    assert judged("AM A1 A2 B1 B", moved) == "fail"
    result = registered()["licence_sides_agree"].run(context("permis_nouveau_recto", both_sides("AM A1 A2 B1 B", moved)))
    assert result.details == {"listed_without_date": ["A1"], "dated_without_listing": ["A"]}
    assert "A1 listée(s) au recto sans date" in result.evidence and "A datée(s) au verso sans être listée(s)" in result.evidence
    assert judged("AM A1 A2 A B1 B", held) == "fail"  # A listed on the front, no date for it on the back
    assert judged("AM A1 A2 B1 B", held, unreadable=("A1",)) == "cannot_tell"  # a date hidden by a reflection
    assert judged("AM A1 A2 B1 B", {}) is None  # the front alone: nothing to compare
    assert outcome("licence_sides_agree", context("permis_etranger", both_sides("AM A1 A2 B1 B", moved))) is None


def licence_line(surname: str = "TESTEUR") -> str:
    """The coded line of a made-up French licence card: 30 characters, the surname in its last eight places."""
    return f"D1FRA13AA000026311231{surname[:8]:<<8}9"


def test_the_coded_line_of_a_french_licence_names_the_holder_printed_on_the_front() -> None:
    def judged(surname, line):
        fields = [field("nom", surname, state=None if surname else "unreadable"), field("mrz", line)]
        return outcome("licence_line_matches_print", context("permis_nouveau_recto", fields))

    assert len(licence_line()) == 30
    assert judged("Testeur", licence_line()) == "pass"
    assert judged("TESTEUR-EXEMPLAIRE", licence_line("TESTEUREXEMPLAIRE")) == "pass"  # a long name is cut in the line
    assert judged("De la Tour", licence_line("DE<LA<TO")) == "pass"  # fillers between the parts of a name
    assert judged("Autrenom", licence_line()) == "fail"  # another name typed on the front
    assert judged("Testeur", licence_line().replace("TESTEUR", "TE5TEUR")) == "pass"  # S read as 5
    assert judged("Testeur", licence_line()[:-2] + "9") == "cannot_tell"  # read one character short
    assert judged("Testeur", None) is None and judged(None, licence_line()) is None  # one side only: not judged
    assert judged("Testeur", "IDFRATESTEUR<<<<<<<<<<<<<<<<<<") is None  # another document's line


# --- paper residence documents ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "copies", "expected"),
    [("1234567890", (), "pass"), ("12 345 678 90", (), "pass"),  # ten digits, printed in groups or not
     ("12345678901", (), "fail"),  # eleven digits
     ("12345678901", ("1234567890",), "pass"),  # one printed copy has ten: the other was misread, not forged
     ("numéro effacé", (), "cannot_tell")],
)
def test_the_foreigner_number_has_ten_digits(number, copies, expected) -> None:
    fields = [field("numero_etranger", number, copies=copies)]
    for doc_type in ("attestation_asile", "attestation_prolongation"):
        assert outcome("foreigner_number", context(doc_type, fields)) == expected
    assert outcome("foreigner_number", context("titre_sejour_recto", fields)) is None  # a card: another numbering


@pytest.mark.parametrize(
    ("issued", "ends", "expected"),
    [("01/03/2026", "31/12/2026", "pass"),  # ten months
     ("01/03/2026", "05/01/2027", "pass"),  # a renewal dated a few days before the previous one ends
     ("01/03/2026", "01/03/2027", "fail"),  # a year
     ("01/03/2026", None, None)],
)
def test_an_asylum_attestation_runs_ten_months_at_most(issued, ends, expected) -> None:
    fields = [field("date_delivrance", issued), field("date_expiration", ends)]
    assert outcome("asylum_validity", context("attestation_asile", fields)) == expected
    assert outcome("asylum_validity", context("attestation_prolongation", fields)) is None  # another document


# --- carte grise -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plate", "dated", "genre", "expected"),
    [("AB-123-CD", "01/03/2015", "MTL", "pass"), ("123 ABC 75", "01/03/2001", "MTL", "pass"),
     ("456 AB 2A", None, "MTT1", "pass"), ("AI-123-CD", None, "MTL", "fail"), ("AB-123-CD", "01/03/2005", "MTL", "fail"),
     ("AB-I23-CD", None, "MTL", "pass"), ("X 12 Y", None, "CL", "cannot_tell"), ("X 12 Y", None, "MTL", "fail")],
)
def test_registration_number_formats(plate, dated, genre, expected) -> None:
    fields = [field("immatriculation", plate), field("date_certificat", dated), field("genre", genre)]
    assert outcome("cg_plate_format", context("carte_grise", fields)) == expected


@pytest.mark.parametrize(
    ("vin", "first", "expected"),
    [("ZZZTEST0123456789", "01/02/2010", "pass"), ("ZZZTESTO123456789", "01/02/2010", "pass"),
     ("ZZZTEST012345678", "01/02/2010", "fail"), ("ZZZTEST012345678", "01/02/1995", None)],
)
def test_vehicle_identification_numbers(vin, first, expected) -> None:
    fields = [field("numero_identification", vin), field("date_premiere_immatriculation", first)]
    assert outcome("cg_vin_format", context("carte_grise", fields)) == expected


# --- 2D-Doc -------------------------------------------------------------------------------------------------------


def twod_doc(surname: str = "TESTEUR", given: str = "ESSAI PREMIER") -> str:
    """A made-up 2D-Doc: header, two data fields, then a signature that happens to spell a name."""
    return f"DC04FR000001FFFFFFFF1301FR60{given}\x1d62{surname}\x1d\x1fAUTRENOMDANSLASIGNATURE"


def with_codes(ctx, *texts: str, kind: str = "DataMatrix"):
    """The same document, whose page carries these codes as the local reader would have recorded them."""
    page = ctx.record.pages[0].model_copy(update={"barcodes": [Barcode(kind=kind, text=text) for text in texts]})
    return dataclasses.replace(ctx, record=ctx.record.model_copy(update={"pages": [page]}))


def test_the_2d_doc_of_a_document_names_the_person_printed_on_it() -> None:
    def judged(fields, *codes, **options):
        return outcome("twod_doc_matches_print", with_codes(context("attestation_prolongation", fields), *codes, **options))

    printed = [field("nom", "Testeur"), field("prenoms", "Essai, Premier")]
    assert judged(printed, twod_doc()) == "pass"
    # another person typed over the document: the code still names the first one (the signature is never searched)
    assert judged([field("nom", "Autrenom"), field("prenoms", "Zoé")], twod_doc()) == "fail"
    # a married or usage name differs from the signed one: one first name in the code is enough
    assert judged([field("nom", "Autrenom"), field("prenoms", "Essai")], twod_doc()) == "pass"
    assert judged([field("nom", state="unreadable"), field("prenoms")], twod_doc()) == "cannot_tell"
    assert judged([field("nom", "Ly"), field("prenoms", "Li")], twod_doc()) == "cannot_tell"  # too short to look for
    # no 2D-Doc on the document (another kind of code, no code at all, or no local reader): nothing is judged
    assert judged(printed, "https://exemple.test/verification", kind="QRCode") is None
    assert judged(printed) is None
    assert outcome("twod_doc_matches_print", context("attestation_prolongation", printed)) is None


def twod_doc_signed(day: date | None) -> str:
    """A made-up 2D-Doc whose header says it was signed on ``day`` (days since 1 January 2000, in hexadecimal)."""
    stamp = "FFFF" if day is None else f"{(day - date(2000, 1, 1)).days:04X}"
    return f"DC04FR000001{stamp}{stamp}1301FR60ESSAI\x1d62TESTEUR\x1d\x1fSIGNATURE"


def test_the_2d_doc_was_signed_when_the_document_says_it_was_issued() -> None:
    def judged(issued, signed):
        ctx = with_codes(context("attestation_prolongation", [field("date_delivrance", issued)]), twod_doc_signed(signed))
        return outcome("twod_doc_date_matches_print", ctx)

    assert judged("06/02/2026", date(2026, 2, 6)) == "pass"
    assert judged("06/02/2026", date(2026, 2, 8)) == "pass"  # a card made two days after its printed date of issue
    assert judged("06/02/2026", date(2024, 2, 20)) == "fail"  # signed two years before the date printed on it
    assert judged("06/02/2026", date(2026, 6, 1)) == "fail"  # or months after it
    assert judged("date effacée", date(2026, 2, 6)) == "cannot_tell"
    assert judged(None, date(2026, 2, 6)) is None  # no printed date of issue to compare
    assert judged("06/02/2026", None) is None  # a code whose header gives no date
    assert outcome("twod_doc_date_matches_print", context("attestation_prolongation", [field("date_delivrance", "06/02/2026")])) is None


@pytest.mark.parametrize(
    ("issued", "ends", "expected"),
    [("20/02/2024", "19/05/2024", "pass"),  # three months
     ("20/02/2024", "24/05/2024", "pass"),  # a few days more: inside the margin
     ("10/03/2025", "09/03/2026", "fail"),  # a year
     ("10/03/2025", None, None)],
)
def test_an_attestation_of_extended_processing_runs_three_months_at_most(issued, ends, expected) -> None:
    fields = [field("date_delivrance", issued), field("date_expiration", ends)]
    titled = {PROLONGATION_TITLE: "present"}
    assert outcome("prolongation_validity", context("attestation_prolongation", fields, elements=titled)) == expected
    assert outcome("prolongation_validity", context("attestation_asile", fields)) is None  # its own rule: ten months


def test_the_three_month_maximum_judges_only_a_paper_that_carries_the_attestation_s_title() -> None:
    """The article sets a maximum for the attestation of extended processing. A paper filed under that kind but
    printed under another title (a récépissé) has no such maximum: the rule says nothing about it."""
    assert PROLONGATION_TITLE in doc_type_named("attestation_prolongation", load_doc_types(DOC_TYPES)).elements
    a_year = [field("date_delivrance", "10/03/2025"), field("date_expiration", "09/03/2026")]
    for state in ("absent", "unreadable"):
        other_paper = context("attestation_prolongation", a_year, elements={PROLONGATION_TITLE: state})
        assert outcome("prolongation_validity", other_paper) is None
    assert outcome("prolongation_validity", context("attestation_prolongation", a_year)) is None  # title not reported


# --- RIB ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bic", "expected"),
    [("ABCDFRPP", "pass"), ("ABCDFRPPXXX", "pass"), ("ABCD FR PP", "pass"), ("AB1DFRPP", "pass"),
     ("ABCDFR", "fail"), ("ABCDFRPPX", "fail")],
)
def test_bic_format(bic, expected) -> None:
    assert outcome("bic_format", context("rib", [field("bic", bic)])) == expected


@pytest.mark.parametrize(
    ("iban", "kind", "expected"),
    [(IBAN, None, "pass"), ("MC00 1234 5678 9012 3456 7890 123", None, "fail"), ("CH00 1234 5678 9012 3456 7", None, "fail"),
     (IBAN, "Livret A", "fail"), (IBAN, "Compte My Pocket", "fail"), (None, None, "cannot_tell")],
)
def test_cases_the_rib_fiche_refuses(iban, kind, expected) -> None:
    assert outcome("rib_refused", context("rib", [field("iban", iban), field("type_compte", kind)])) == expected


# --- relevé -------------------------------------------------------------------------------------------------------


def test_the_relevé_type_asks_for_the_sections() -> None:
    assert SECTIONS in doc_type_named("releve", load_doc_types(DOC_TYPES)).elements


@pytest.mark.parametrize(
    ("edited", "sections", "expected"),
    [("01/09/2025", "absent", "fail"), ("01/09/2025", "present", "pass"), ("01/05/2024", "absent", "pass"),
     ("01/09/2025", None, "cannot_tell")],
)
def test_a_relevé_edited_since_july_2025_follows_the_european_model(edited, sections, expected) -> None:
    elements = {SECTIONS: sections} if sections else {}
    assert outcome("releve_new_model", context("releve", [field("date_edition", edited)], elements=elements)) == expected


def test_an_incomplete_copy_of_the_european_model_is_left_to_a_person() -> None:
    """The two-language wording belongs to the new model: sections missing from the copy do not make an old form."""
    new_model = [field("date_edition", "01/09/2025"), field("sinistres", "NEANT / NIL")]
    assert outcome("releve_new_model", context("releve", new_model, elements={SECTIONS: "absent"})) == "cannot_tell"
    old_model = [field("date_edition", "01/09/2025"), field("sinistres", "Néant")]
    assert outcome("releve_new_model", context("releve", old_model, elements={SECTIONS: "absent"})) == "fail"


def bonus_fields(coefficient: str, licence: str, vehicle: str | None) -> list:
    return [field("bonus_malus", coefficient), field("date_permis", licence), field("date_edition", "01/09/2026"),
            field("date_souscription", "01/02/2024"), field("vehicule", vehicle)]


@pytest.mark.parametrize("vehicle", ["Moto 650", "Scooter 125", "B : MOTOCYCLE", "Trail 689 cm3"])
def test_a_riders_bonus_is_never_judged_against_the_motorcycle_licence(vehicle) -> None:
    """The coefficient follows the driver from a car to a motorcycle (annex to article A121-1, article 10):
    0.50 next to a motorcycle licence of two years is lawful, so no rule may fail on it."""
    assert "bonus_malus_history" not in registered()
    ctx = context("releve", bonus_fields("0,50", "01/01/2024", vehicle))
    assert [name for name, check in registered().items() if check.applies(ctx) and check.run(ctx).outcome == "fail"] == []
    assert outcome("bonus_malus_licence", ctx) == "cannot_tell"


@pytest.mark.parametrize(
    ("coefficient", "licence", "vehicle", "expected"),
    [("0,50", "01/01/2024", "Voiture citadine", "fail"),  # two years of licence: 0.90 at the lowest
     ("0,90", "01/01/2024", "Voiture citadine", "pass"),
     ("50 %", "01/01/2024", "Voiture citadine", "fail"),  # the same coefficient printed as a percentage
     ("0,50", "01/01/2010", "Voiture citadine", "pass"),  # sixteen years: 0.50 is reached after thirteen
     ("0,50", "12/06/2024 (A1) 04/09/2009 (B)", "Voiture citadine", "pass"),  # the earliest licence counts
     ("0,50", "03/2009", "Voiture citadine", "pass"),  # a licence printed as month and year
     ("0,50", "01/01/2024", None, "cannot_tell"),  # the relevé does not say what the vehicle is: not judged
     ("0,50", "01/01/2024", "AB-123-CD", "cannot_tell"),  # a plate alone does not say it either
     ("0,50", "01/01/2024", "A / MARQUE / AB-123-CD", "fail"),  # the European model's letter for a car
     ("0,50", "01/01/2024", "RENAULT MODELE 1.5", "fail"),  # a make that builds cars only
     ("0,50", "01/01/2027", "Voiture citadine", "cannot_tell")],  # a licence dated after the relevé: left to `dates`
)
def test_a_car_drivers_bonus_cannot_be_lower_than_the_licence_allows(coefficient, licence, vehicle, expected) -> None:
    assert outcome("bonus_malus_licence", context("releve", bonus_fields(coefficient, licence, vehicle))) == expected


def test_the_lowest_coefficient_follows_the_yearly_reduction() -> None:
    from fraudcheck.checks.family.releve_rules import earliest_date, lowest_coefficient

    assert [lowest_coefficient(years) for years in (0, 1, 2, 5, 12, 13, 40)] == [1.0, 0.95, 0.9, 0.76, 0.51, 0.5, 0.5]
    assert earliest_date("12/06/2024 (A1) 04/09/2011 (B)", 2026).isoformat() == "2011-09-04"
    assert earliest_date("04/1997", 2026).isoformat() == "1997-04-01" and earliest_date("permis B", 2026) is None


# --- carte grise lines ---------------------------------------------------------------------------------------------


def cg_lines(plate="AB123CD", vin="ZZZTEST0123456789", first="150301", make="TESTMOTO", formula="2015AB01234") -> str:
    """The two lines at the bottom of a certificate, built from invented values, check digits computed."""
    one = f"CRFRA{plate}{check_digit(plate)}{vin}{check_digit(vin)}{first}{check_digit(first)}L3E<<<"  # 44 characters
    two = f"MOTO<{make}<MODELE<{formula}<<<<<12"
    return one + chr(10) + two


CARTE_GRISE = [field("immatriculation", "AB-123-CD"), field("numero_identification", "ZZZTEST0123456789"),
               field("date_premiere_immatriculation", "01/03/2015"), field("marque", "Testmoto"),
               field("numero_formule", "2015AB01234")]


def test_the_lines_of_a_carte_grise_are_read_at_fixed_places() -> None:
    lines = parse_lines(cg_lines())
    assert (lines.plate, lines.vin, lines.first) == ("AB123CD", "ZZZTEST0123456789", "150301") and not lines.failed()
    assert parse_lines(cg_lines(plate="A8123CD")).plate == "AB123CD"  # an 8 where a letter must be is a B
    assert parse_lines("CRFRA1234AB75<" + "0" * 30) is None  # a former plate: places unknown
    assert parse_lines("pas une ligne") is None


def test_carte_grise_check_digits() -> None:
    assert outcome("cg_mrz_check_digits", context("carte_grise", [field("mrz", cg_lines())])) == "pass"
    edited = cg_lines().replace("150301", "160301", 1)  # the date changed without its check digit
    assert outcome("cg_mrz_check_digits", context("carte_grise", [field("mrz", edited)])) == "fail"
    misread = cg_lines().replace("ZZZTEST0123456789", "ZZZTESTO123456789", 1)
    assert outcome("cg_mrz_check_digits", context("carte_grise", [field("mrz", misread)])) == "pass"
    assert outcome("cg_mrz_check_digits", context("carte_grise", [field("mrz", state="unreadable")])) == "cannot_tell"
    assert outcome("cg_mrz_check_digits", context("carte_grise", CARTE_GRISE)) is None  # no lines read


def test_carte_grise_lines_agree_with_the_printed_values() -> None:
    assert outcome("cg_mrz_matches_print", context("carte_grise", [*CARTE_GRISE, field("mrz", cg_lines())])) == "pass"
    for changed in (cg_lines(plate="AB124CD"), cg_lines(vin="ZZZTEST0123456780"), cg_lines(first="160301"),
                    cg_lines(make="AUTREMARQUE"), cg_lines(formula="2015AB09999")):
        assert outcome("cg_mrz_matches_print", context("carte_grise", [*CARTE_GRISE, field("mrz", changed)])) == "fail"
    only_lines = context("carte_grise", [field("mrz", cg_lines())])
    assert outcome("cg_mrz_matches_print", only_lines) == "cannot_tell"


def test_a_first_line_read_with_a_character_missing_is_not_judged() -> None:
    """Line 1 is read at fixed places: one character dropped by the reader shifts every value after it, and the
    check digits of a certificate that is right would then look wrong."""
    one, two = cg_lines().split(chr(10))
    short = one[:20] + one[21:] + chr(10) + two  # 43 characters instead of 44
    for check in ("cg_mrz_check_digits", "cg_mrz_matches_print"):
        assert outcome(check, context("carte_grise", [*CARTE_GRISE, field("mrz", short)])) == "cannot_tell"


# --- relevé: the vehicle's category against its maker ---------------------------------------------------------------

TWO_WHEELER_NUMBER = "JYATEST0000000001"  # 17 characters starting with a maker code of two-wheelers (made up)
CAR_NUMBER = "VF7TEST0000000001"  # 17 characters starting with a maker code of cars (made up)


@pytest.mark.parametrize(
    ("vehicle", "expected"),
    [(f"A / MARQUE / {CAR_NUMBER}", "car"), ("A MARQUE AB-123-CD", "car"), ("A-F / MARQUE", "car"),
     ("B MARQUE AB-123-CD", "two_wheeler"), ("D / MARQUE", "two_wheeler"),
     ("Voiture citadine", "car"), ("AUTOMOBILE MARQUE", "car"),
     ("Trail 689 cm³", "two_wheeler"), ("2 ROUES / MARQUE", "two_wheeler"), ("1 VÉHICULE : 1 MOTO", "two_wheeler"),
     ("YAMAHA MODELE", "two_wheeler"), ("Harley-Davidson MODELE", "two_wheeler"), ("MOTO GUZZI MODELE", "two_wheeler"),
     ("RENAULT MODELE 1.5", "car"), ("CITROËN MODELE", "car"), ("ALFA ROMEO MODELE", "car"),
     ("HONDA MODELE", None), ("PEUGEOT 208", None),  # makers of both kinds: the name alone says nothing
     (f"A HONDA MODELE JH2TEST0000000001", "two_wheeler"),  # the maker code of a two-wheeler wins over the letter
     ("MARQUE INCONNUE MODELE", None), ("AB-123-CD", None), ("", None), (None, None),
     ("123 SYM 75", None), ("12 KTM 34 / MARQUE", None)],  # the letters of an old plate are not a make
)
def test_the_kind_of_vehicle_a_releve_describes(vehicle, expected) -> None:
    from fraudcheck.checks.family.releve_rules import vehicle_kind

    assert vehicle_kind(vehicle, str(REPO_ROOT / "config" / "vehicle_makers.toml")) == expected


def test_no_model_name_decides_the_kind_of_a_vehicle() -> None:
    """The lists hold makers, never models: a list of models is never complete, and one taken from the documents at
    hand would fit them only. A maker of both kinds stays out of both lists."""
    import tomllib

    makers = tomllib.loads((REPO_ROOT / "config" / "vehicle_makers.toml").read_text(encoding="utf-8"))
    both_kinds = {"honda", "suzuki", "bmw", "peugeot", "seat"}
    assert not both_kinds & (set(makers["two_wheeler_makes"]) | set(makers["car_makes"]))
    assert not set(makers["two_wheeler_makes"]) & set(makers["car_makes"])
    assert not set(makers["two_wheelers"]) & set(makers["cars"])


@pytest.mark.parametrize(
    ("vehicle", "expected"),
    [(f"A\nMARQUE\n{TWO_WHEELER_NUMBER}\nAB-123-CD", "fail"),  # classed as a car, built by a maker of two-wheelers
     (f"B / MARQUE / {TWO_WHEELER_NUMBER}", "pass"),
     (f"D / MARQUE / {TWO_WHEELER_NUMBER}", "pass"),  # a moped
     (f"A MARQUE {CAR_NUMBER} AB-123-CD", "pass"),
     (f"A : VOITURE / MARQUE / {CAR_NUMBER}", "pass"),  # the letter printed with its meaning
     (f"B / MARQUE / {CAR_NUMBER}", "fail"),  # classed as a motorcycle, built by a maker of cars
     (f"A-F / MARQUE / {CAR_NUMBER}", "pass"),  # a car and its trailer
     (f"A-B / MARQUE / {TWO_WHEELER_NUMBER}", "pass"),  # several vehicles on one relevé: nothing to conclude
     ("A / MARQUE / XXXTEST0000000001", "cannot_tell"),  # a maker that is not listed
     ("A MARQUE AB-123-CD", "cannot_tell"),  # no identification number
     (f"MARQUE MODELE {TWO_WHEELER_NUMBER}", "cannot_tell"),  # no category letter
     (f"A 123 B / MARQUE / {TWO_WHEELER_NUMBER}", "cannot_tell"),  # an old plate comes first: not a category letter
     (f"AUTOMOBILE MARQUE {TWO_WHEELER_NUMBER}", "cannot_tell"),  # a word, not a letter
     (None, None)],
)
def test_the_vehicle_category_of_a_releve_agrees_with_its_maker(vehicle, expected) -> None:
    assert outcome("releve_vehicle_category", context("releve", [field("vehicule", vehicle)])) == expected
    assert outcome("releve_vehicle_category", context("carte_grise", [field("vehicule", vehicle)])) is None


@pytest.mark.parametrize(
    ("letter", "number", "expected"),
    [("A", TWO_WHEELER_NUMBER, "fail"), ("B", TWO_WHEELER_NUMBER, "pass"), ("A", CAR_NUMBER, "pass"),
     ("B", CAR_NUMBER, "fail"), ("A", None, "cannot_tell"), (None, TWO_WHEELER_NUMBER, "cannot_tell")],
)
def test_the_category_and_the_number_are_read_from_their_own_fields_when_the_reader_returns_them(
        letter, number, expected) -> None:
    """Items 9 and 11 of the European model are asked as fields of their own: the rule then does not depend on how
    the free description is written. A reading made before these fields existed still works through the description."""
    fields = [field("categorie_vehicule", letter), field("vehicule", "MARQUE MODELE AB-123-CD"),
              field("numero_identification_vehicule", number)]
    assert outcome("releve_vehicle_category", context("releve", fields)) == expected
    # the letter of the dedicated field comes first: a letter met later in the description does not replace it
    both = [field("categorie_vehicule", "B"), field("vehicule", f"A / MARQUE / {TWO_WHEELER_NUMBER}")]
    assert outcome("releve_vehicle_category", context("releve", both)) == "pass"
    car = [*bonus_fields("0,50", "01/01/2024", "MARQUE MODELE"), field("categorie_vehicule", "A")]
    assert outcome("bonus_malus_licence", context("releve", car)) == "fail"  # said to be a car: judged


# --- foreign licence: the length of the number ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "number", "copies", "expected"),
    [("REPUBBLICA ITALIANA", "AB1234567C", (), "pass"), ("REPUBBLICA ITALIANA", "AB 1234567 C", (), "pass"),
     ("Italie", "AB1234567CD", (), "fail"),  # eleven characters
     ("REPUBBLICA ITALIANA", "AB1234567CD", ("AB1234567C",), "pass"),  # one printed copy has ten: the other was misread
     ("REPUBBLICA ITALIANA", "- - -", (), "cannot_tell"),
     ("REINO DE ESPAÑA", "12345678-Z", (), "pass"), ("REINO DE ESPAÑA", "X1234567-Z", (), "pass"),  # nine in Spain
     ("REINO DE ESPAÑA", "123456789Z", (), "fail"),  # ten characters on a Spanish licence
     ("REPÚBLICA PORTUGUESA", "L-1234567 8", (), None),  # a country whose numbering is not listed: not judged
     (None, "AB1234567CD", (), None)],  # a back does not name the country
)
def test_a_foreign_licence_number_has_the_length_its_country_gives_it(country, number, copies, expected) -> None:
    fields = [field("pays", country), field("numero_permis", number, copies=copies)]
    assert outcome("foreign_licence_number", context("permis_etranger", fields)) == expected
    assert outcome("foreign_licence_number", context("permis_nouveau_recto", fields)) is None  # French cards: not judged
