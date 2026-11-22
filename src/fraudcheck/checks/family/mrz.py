"""Identity documents and residence permits: the machine-readable lines (MRZ).

mrz_check_digits: every check digit of the lines is right (ICAO Doc 9303, and the French
identity card of 1995). Someone who edits the lines by hand rarely recomputes them.
mrz_matches_print: the lines and the printed fields of the same document agree: document
number, birth date, expiry date, surname and given names. A surname found where the
lines put the given names, and the reverse, is reported as names in the wrong order.

Names in the lines carry no accents and may be cut short, so names are compared
without accents, spaces or punctuation, and a name that is the start of the other
counts as the same. Before a failure counts, the usual single-character misreads are tried.
"""

from __future__ import annotations

import re
import unicodedata

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._mrz import Mrz, clean, parse
from fraudcheck.checks.family._values import (
    ReadingCheck,
    compact,
    misread_note,
    parse_date,
    passes_after_misread,
    value,
)

_LIGATURES = {"Œ": "OE", "œ": "OE", "Æ": "AE", "æ": "AE", "ß": "SS"}
_SAME_DIGIT = str.maketrans("OIBS", "0185")


def name_key(text: str | None) -> str:
    """Letters only, upper case, no accents; UE, OE and AE as U, O and A (the lines may write either)."""
    if not text:
        return ""
    text = "".join(_LIGATURES.get(char, char) for char in text)
    text = unicodedata.normalize("NFKD", text)
    letters = re.sub(r"[^A-Z]", "", "".join(char for char in text if not unicodedata.combining(char)).upper())
    return letters.replace("UE", "U").replace("OE", "O").replace("AE", "A")


def same_name(one: str, two: str) -> bool:
    """Equal, or one is the start of the other (the lines cut long names short)."""
    return bool(one) and bool(two) and (one.startswith(two) or two.startswith(one))


def number_key(text: str) -> str:
    """A document number as compared: upper case, letters and digits only, usual misreads made equal."""
    return re.sub(r"[^A-Z0-9]", "", compact(text)).translate(_SAME_DIGIT)


def _all_right(candidate: str) -> bool:
    mrz = parse(candidate)
    return mrz is not None and not mrz.failed


class MrzCheck(ReadingCheck):
    families = frozenset({"identity"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "mrz")[1] in ("read", "unreadable")

    def lines(self, context: DocumentContext) -> tuple[Mrz | None, CheckResult | None]:
        text, state = value(context, "mrz")
        if state != "read":
            return None, self.result("cannot_tell", "Lignes de lecture automatique illisibles.", fields=["mrz"])
        mrz = parse(text)
        if mrz is None:
            return None, self.result("cannot_tell", "Les lignes de lecture automatique lues n'ont pas une longueur connue "
                                     "(lecture incomplète ou modèle non reconnu).", fields=["mrz"])
        return mrz, None


@register
class MrzCheckDigits(MrzCheck):
    name = "mrz_check_digits"
    source = "general"
    description = "Identity: every check digit of the machine-readable lines is right (ICAO 9303, French card of 1995)."

    def run(self, context: DocumentContext) -> CheckResult:
        mrz, blocked = self.lines(context)
        if blocked:
            return blocked
        details = {"layout": mrz.layout, "checked": [item.name for item in mrz.checks]}
        if not mrz.failed:
            return self.result("pass", f"Les {len(mrz.checks)} chiffres de contrôle des lignes de lecture automatique "
                               f"({mrz.layout}) sont justes.", fields=["mrz"], details=details)
        if found := passes_after_misread(clean(value(context, "mrz")[0]), _all_right):
            return self.result("pass", f"Les chiffres de contrôle des lignes de lecture automatique ({mrz.layout}) sont "
                               f"justes{misread_note(found)}.", fields=["mrz"], details=details)
        failed = [item.name for item in mrz.failed]
        return self.result(
            "fail",
            f"Chiffre(s) de contrôle faux dans les lignes de lecture automatique ({mrz.layout}) : {', '.join(failed)}.",
            fields=["mrz"],
            clues=["chiffre de contrôle faux dans les lignes de lecture automatique"],
            fraud_type="lignes de lecture automatique modifiées",
            details={**details, "failed": failed},
        )


@register
class MrzMatchesPrint(MrzCheck):
    name = "mrz_matches_print"
    source = "general"
    description = "Identity: the machine-readable lines agree with the printed number, dates and names, in that order."

    def run(self, context: DocumentContext) -> CheckResult:
        mrz, blocked = self.lines(context)
        if blocked:
            return blocked
        compared, differing = [], []
        number, state = value(context, "numero_document")
        if state == "read" and mrz.number:
            compared.append("numero_document")
            printed, coded = number_key(number), number_key(mrz.number)
            if not (printed == coded or (len(coded) >= 9 and printed.startswith(coded))):
                differing.append(("numero_document", "le numéro du document"))
        for field, coded, label, ahead in (("date_naissance", mrz.birth, "la date de naissance", 0),
                                           ("date_expiration", mrz.expiry, "la date d'expiration", 30)):
            text, state = value(context, field)
            printed = parse_date(text, context.settings.today().year + ahead).value if state == "read" else None
            if printed is None or not re.fullmatch(r"\d{6}", coded or ""):
                continue
            compared.append(field)
            if printed.strftime("%y%m%d") != coded:
                differing.append((field, label))
        swapped = self._names(context, mrz, compared, differing)
        fields = sorted({*compared, "mrz"})
        if swapped:
            return self.result("fail", "Le nom et les prénoms imprimés sont inversés par rapport aux lignes de lecture "
                               "automatique.", fields=["nom", "prenoms", "mrz"], clues=["nom et prénoms inversés"],
                               fraud_type="identité modifiée", details={"layout": mrz.layout})
        if differing:
            return self.result(
                "fail",
                "Les lignes de lecture automatique ne disent pas la même chose que le texte imprimé pour "
                + ", ".join(label for _, label in differing) + ".",
                fields=sorted({field for field, _ in differing} | {"mrz"}),
                clues=["lignes de lecture automatique différentes du texte imprimé"],
                fraud_type="valeur modifiée",
                details={"layout": mrz.layout, "differing": [field for field, _ in differing], "compared": compared},
            )
        if not compared:
            return self.result("cannot_tell", "Aucune valeur imprimée à comparer aux lignes de lecture automatique.",
                               fields=["mrz"], details={"layout": mrz.layout})
        return self.result("pass", f"Les lignes de lecture automatique concordent avec le texte imprimé "
                           f"({len(compared)} valeur(s) comparée(s)).", fields=fields,
                           details={"layout": mrz.layout, "compared": compared})

    @staticmethod
    def _names(context: DocumentContext, mrz: Mrz, compared: list[str], differing: list[tuple[str, str]]) -> bool:
        """Compare surname and given names; ``True`` when they are printed the wrong way round."""
        surname_text, surname_state = value(context, "nom")
        given_text, given_state = value(context, "prenoms")
        surname = name_key(surname_text) if surname_state == "read" else ""
        given = name_key(given_text) if given_state == "read" else ""
        coded_surname, coded_given = name_key(mrz.surname), name_key(mrz.given_names)
        if surname and coded_surname:
            compared.append("nom")
            if not same_name(surname, coded_surname):
                if given and coded_given and same_name(given, coded_surname) and same_name(surname, coded_given):
                    return True
                differing.append(("nom", "le nom"))
        if given and coded_given:
            compared.append("prenoms")
            if not same_name(given, coded_given):
                differing.append(("prenoms", "les prénoms"))
        return False

