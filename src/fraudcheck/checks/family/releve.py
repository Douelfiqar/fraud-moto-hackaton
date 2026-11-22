"""Relevé d'information: the five items of the fiche, and the bonus-malus bounds."""

from __future__ import annotations

import re

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, label, normalise, parse_number, value

#: The five items staff check on every relevé (fiches « Lecture des RI », « Relevé d'informations »).
ITEMS = ("date_edition", "date_souscription", "situation_contrat", "bonus_malus", "vehicule")
#: The fiche says several insurers print no status and the contract is then taken as running.
#: Mopeds and light motorcycles (50 and 125 cm3) are outside the bonus-malus clause unless the contract says
#: otherwise (article A121-1 of the Code des assurances): their relevé may print no coefficient.
OPTIONAL_WHEN_ABSENT = {"situation_contrat", "bonus_malus"}
BONUS_MIN, BONUS_MAX = 0.50, 3.50  # bounds of the French reduction-majoration coefficient


@register
class ReleveItems(ReadingCheck):
    name = "releve_items"
    source = "fiche"
    description = "Relevé: edition date, subscription date, contract status, bonus-malus and vehicle are present."
    families = frozenset({"releve"})

    def run(self, context: DocumentContext) -> CheckResult:
        states = {item: value(context, item)[1] for item in ITEMS}
        if states["vehicule"] != "read" and value(context, "immatriculation")[1] == "read":
            states["vehicule"] = "read"  # a plate alone identifies the vehicle (fiche: MMA, Crédit Mutuel)
        absent = [item for item, state in states.items() if state == "absent" and item not in OPTIONAL_WHEN_ABSENT]
        unsure = [item for item, state in states.items() if state in ("unreadable", "missing")]
        note = (" La situation du contrat n'est pas imprimée, ce que la fiche admet pour plusieurs assureurs "
                "(contrat alors considéré en cours).") if states["situation_contrat"] == "absent" else ""
        if absent:
            names = ", ".join(label(context, item) for item in absent)
            return self.result("fail", f"Élément obligatoire absent du relevé : {names}.{note}", fields=absent,
                               clues=[f"absent : {label(context, item)}" for item in absent])
        if unsure:
            return self.result("cannot_tell", "Élément du relevé illisible : "
                               + ", ".join(label(context, item) for item in unsure) + f".{note}", fields=unsure)
        return self.result("pass", f"Les cinq éléments de la fiche sont présents.{note}", fields=list(ITEMS))


def bonus_coefficient(text: str) -> float | None:
    """The coefficient from a printed bonus-malus: '0,68', '50 %', 'bonus 50 %' (0.50), 'malus 25 %' (1.25)."""
    text = re.sub(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", " ", text)  # a date next to the coefficient is not it
    number = parse_number(text)
    if number is None:
        return None
    if 0 < number < 5:
        return number  # "0,50 (50 %)", "0,64 soit 36 % de bonus": the coefficient is printed as such
    words = normalise(text)
    is_percent = "%" in text or bool(re.search(r"\bpour ?cent\b", words))
    down = "bonus" in words or "reduction" in words
    up = "malus" in words or "majoration" in words
    if is_percent and down and not up:
        # "bonus 50 %" is a reduction of 50 %; no reduction exceeds 50 %, so "bonus 90 %" is the coefficient itself
        return round(1 - number / 100, 4) if number <= 50 else round(number / 100, 4)
    if is_percent and up and not down:
        return round(1 + number / 100, 4)
    if is_percent or 30 <= number <= 350:
        return round(number / 100, 4)
    return number


@register
class BonusMalusRange(ReadingCheck):
    name = "bonus_malus_range"
    source = "general"
    description = "Relevé: the bonus-malus coefficient lies between 0.50 and 3.50."
    families = frozenset({"releve"})

    def run(self, context: DocumentContext) -> CheckResult:
        text, state = value(context, "bonus_malus")
        fields = ["bonus_malus"]
        if state != "read":
            return self.result("cannot_tell", "Coefficient bonus-malus absent ou illisible.", fields=fields)
        coefficient = bonus_coefficient(text)
        if coefficient is None:
            return self.result("cannot_tell", "Le coefficient bonus-malus imprimé n'a pas pu être compris.", fields=fields)
        if not BONUS_MIN <= coefficient <= BONUS_MAX:
            return self.result("fail", "Le coefficient bonus-malus sort des bornes légales (0,50 à 3,50).",
                               fields=fields, clues=["bonus-malus hors des bornes légales"],
                               details={"coefficient": coefficient})
        return self.result("pass", "Le coefficient bonus-malus est dans les bornes légales (0,50 à 3,50).",
                           fields=fields, details={"coefficient": coefficient})
