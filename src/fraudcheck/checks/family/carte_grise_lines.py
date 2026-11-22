"""Carte grise: the two machine-readable lines at the bottom of the certificate.

On a French registration certificate,
line 1 holds "CRFRA", the plate, its check digit, the VIN, its check digit, the date of
first registration as YYMMDD, its check digit, then the category; line 2 holds the body
type, the make, the model, the formula number and check digits. The check digits follow
the travel-document rule (weights 7, 3, 1; A = 10 ... Z = 35; filler 0; modulo 10).

cg_mrz_check_digits: the three check digits of line 1 are right. The final digits of
line 2 are left out: their rule was not verified.
cg_mrz_matches_print: the lines agree with the printed plate, VIN, first registration,
make and formula number.

Line 1 is read at fixed places only when the plate has the current format (seven
characters); any other plate gives "cannot tell". Usual misreads are undone first: a
digit read where the plate must have a letter, a letter where it must have a digit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._mrz import check_digit, clean, digits_only
from fraudcheck.checks.family._values import ReadingCheck, misread_note, parse_date, passes_after_misread, value
from fraudcheck.checks.family.carte_grise_numbers import SIV, plate_key

#: Each of the two lines of the certificate holds this many characters.
LINE_LENGTH = 44
_AS_LETTER = str.maketrans("0158", "OISB")
_SAME = str.maketrans("OIBSQ", "01850")


@dataclass(frozen=True)
class Lines:
    plate: str
    plate_check: str
    vin: str
    vin_check: str
    first: str  # YYMMDD
    first_check: str
    rest: str  # the category, then line 2

    def checks(self) -> list[tuple[str, str, str]]:
        return [("numéro d'immatriculation", self.plate, self.plate_check),
                ("numéro d'identification du véhicule", self.vin, self.vin_check),
                ("date de première immatriculation", self.first, self.first_check)]

    def failed(self) -> list[str]:
        return [name for name, data, printed in self.checks()
                if not (printed.isdigit() and check_digit(data) == int(printed))]


def current_plate(text: str) -> str | None:
    """Seven characters read as a current-format plate, usual misreads undone; ``None`` when they cannot be one."""
    if len(text) != 7:
        return None
    plate = text[:2].translate(_AS_LETTER) + digits_only(text[2:5]) + text[5:].translate(_AS_LETTER)
    return plate if SIV.fullmatch(plate) else None


def parse_lines(text: str | None) -> Lines | None:
    flat = clean(text or "")
    if not flat.startswith("CRFRA") or len(flat) < 38 or not re.fullmatch(r"[A-Z0-9<]+", flat):
        return None
    plate = current_plate(flat[5:12])
    if plate is None:
        return None
    return Lines(plate, digits_only(flat[12]), flat[13:30], digits_only(flat[30]), digits_only(flat[31:37]),
                 digits_only(flat[37]), flat[38:])


def _letters(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^A-Z]", "", "".join(char for char in text if not unicodedata.combining(char)).upper())


def _code(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper()).translate(_SAME)


def _all_right(candidate: str) -> bool:
    lines = parse_lines(candidate)
    return lines is not None and not lines.failed()


class LinesCheck(ReadingCheck):
    families = frozenset({"carte_grise"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "mrz")[1] in ("read", "unreadable")

    def lines(self, context: DocumentContext) -> tuple[Lines | None, CheckResult | None]:
        text, state = value(context, "mrz")
        if state != "read":
            return None, self.result("cannot_tell", "Lignes de lecture automatique illisibles.", fields=["mrz"])
        rows = [clean(row) for row in text.splitlines() if row.strip()]
        if len(rows) >= 2 and len(rows[0]) != LINE_LENGTH:
            # Line 1 is read at fixed places: one character dropped or doubled by the reader shifts every
            # value after it, and the check digits then look wrong on a certificate that is right.
            return None, self.result("cannot_tell", f"La première ligne de lecture automatique a été lue avec "
                                     f"{len(rows[0])} caractères au lieu de {LINE_LENGTH} : lecture incomplète, ses "
                                     "valeurs ne peuvent pas être jugées à leur place.", fields=["mrz"])
        lines = parse_lines(text)
        if lines is None:
            return None, self.result("cannot_tell", "Lignes de lecture automatique d'un format non reconnu (elles "
                                     "commencent par CRFRA et une plaque au format actuel).", fields=["mrz"])
        return lines, None


@register
class CarteGriseLinesCheckDigits(LinesCheck):
    name = "cg_mrz_check_digits"
    source = "general"
    description = "Carte grise: the check digits of the plate, VIN and first registration in the lines are right."

    def run(self, context: DocumentContext) -> CheckResult:
        lines, blocked = self.lines(context)
        if blocked:
            return blocked
        failed = lines.failed()
        if not failed:
            return self.result("pass", "Les chiffres de contrôle de la plaque, du numéro d'identification et de la date "
                               "de première immatriculation sont justes.", fields=["mrz"])
        if found := passes_after_misread(clean(value(context, "mrz")[0]), _all_right):
            return self.result("pass", "Les chiffres de contrôle des lignes de lecture automatique sont justes"
                               f"{misread_note(found)}.", fields=["mrz"])
        return self.result("fail", "Chiffre(s) de contrôle faux dans les lignes de lecture automatique du certificat : "
                           + ", ".join(failed) + ".", fields=["mrz"],
                           clues=["chiffre de contrôle faux dans les lignes de la carte grise"],
                           fraud_type="lignes de lecture automatique modifiées", details={"failed": failed})


@register
class CarteGriseLinesMatchPrint(LinesCheck):
    name = "cg_mrz_matches_print"
    source = "general"
    description = "Carte grise: the lines agree with the printed plate, VIN, first registration, make and formula number."

    def run(self, context: DocumentContext) -> CheckResult:
        lines, blocked = self.lines(context)
        if blocked:
            return blocked
        pivot = context.settings.today().year
        compared, differing = [], []

        def compare(field: str, label: str, agree) -> None:
            text, state = value(context, field)
            if state == "read":
                outcome = agree(text)
                if outcome is not None:
                    compared.append(field)
                    if not outcome:
                        differing.append((field, label))

        compare("immatriculation", "le numéro d'immatriculation",
                lambda text: _code(plate_key(text)) == _code(lines.plate))
        compare("numero_identification", "le numéro d'identification du véhicule",
                lambda text: _code(text) == _code(lines.vin.rstrip("<")))
        compare("date_premiere_immatriculation", "la date de première immatriculation",
                lambda text: None if (day := parse_date(text, pivot).value) is None else day.strftime("%y%m%d") == lines.first)
        rest_letters, rest_code = _letters(lines.rest), _code(lines.rest)
        compare("marque", "la marque",
                lambda text: None if not (make := _letters(text)) else make[:6] in rest_letters)
        compare("numero_formule", "le numéro de formule",
                lambda text: None if len(number := _code(text)) < 5 else number in rest_code)
        fields = sorted({*compared, "mrz"})
        if differing:
            return self.result(
                "fail",
                "Les lignes de lecture automatique du certificat ne disent pas la même chose que le texte imprimé pour "
                + ", ".join(label for _, label in differing) + ".",
                fields=sorted({field for field, _ in differing} | {"mrz"}),
                clues=["lignes de la carte grise différentes du texte imprimé"],
                fraud_type="valeur modifiée",
                details={"differing": [field for field, _ in differing], "compared": compared},
            )
        if not compared:
            return self.result("cannot_tell", "Aucune valeur imprimée à comparer aux lignes de lecture automatique.",
                               fields=["mrz"])
        return self.result("pass", f"Les lignes de lecture automatique concordent avec le texte imprimé "
                           f"({len(compared)} valeur(s) comparée(s)).", fields=fields, details={"compared": compared})
