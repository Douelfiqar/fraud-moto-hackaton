"""Checks for every family: dates exist and come in a possible order; copies of a value agree."""

from __future__ import annotations

import re

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import (
    ReadingCheck,
    doc_type,
    label,
    malformed_date,
    parse_date,
    same_value,
    value,
)


#: Dates that can lie in the future: "28.02.28" on such a field is 2028, not 1928.
_FUTURE = re.compile(r"expir|validit|resiliation|echeance|fin_")
_FUTURE_YEARS = 30


@register
class DatesInOrder(ReadingCheck):
    name = "dates"
    source = "general"
    description = "Every date exists in the calendar, and dates come in a possible order (config/doc_types)."

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return super().applies(context) and kind is not None and bool(kind.field_names("date"))

    def run(self, context: DocumentContext) -> CheckResult:
        kind = doc_type(context)
        pivot = context.settings.today().year
        parsed, impossible, malformed = {}, [], []
        for name in kind.field_names("date"):
            text, state = value(context, name)
            if state != "read":
                continue
            if malformed_date(text):
                malformed.append(name)
            date_value = parse_date(text, pivot + _FUTURE_YEARS if _FUTURE.search(name) else pivot)
            if date_value.impossible:
                impossible.append(name)
            elif date_value.value is not None:
                parsed[name] = date_value.value
        disorder = [(first, second) for first, second in kind.date_order
                    if first in parsed and second in parsed and parsed[first] > parsed[second]]
        checked = [pair for pair in kind.date_order if pair[0] in parsed and pair[1] in parsed]
        if impossible or disorder or malformed:
            parts = [f"la date « {label(context, name)} » n'existe pas au calendrier" for name in impossible]
            parts += [f"« {label(context, first)} » est postérieure à « {label(context, second)} »" for first, second in disorder]
            parts += [f"la date « {label(context, name)} » est imprimée avec un séparateur en trop, ce qu'aucun "
                      "logiciel d'édition ne produit" for name in malformed]
            fields = sorted({*impossible, *malformed, *(name for pair in disorder for name in pair)})
            clue = ("date impossible" if impossible else "dates dans un ordre impossible" if disorder
                    else "date mal formée (séparateur en trop)")
            return self.result("fail", "Dates incohérentes : " + " ; ".join(parts) + ".", fields=fields, clues=[clue])
        if not parsed:
            return self.result("cannot_tell", "Aucune date lisible à vérifier.", fields=kind.field_names("date"))
        return self.result("pass", f"{len(parsed)} date(s) lue(s), toutes valides ; {len(checked)} ordre(s) vérifié(s).",
                           fields=sorted(parsed))


@register
class CopiesAgree(ReadingCheck):
    name = "copies_agree"
    source = "general"
    description = "Every copy of a value printed more than once says the same thing (the form it is printed in aside)."

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and any(
            item.state == "read" and item.value and item.other_copies for item in context.reading.answer.fields
        )

    def run(self, context: DocumentContext) -> CheckResult:
        pivot = context.settings.today().year
        kind = doc_type(context)
        free_text = {*kind.field_names("text"), *kind.field_names("number")} if kind else set()
        compared, differing = [], []
        for item in context.reading.answer.fields:
            if item.state != "read" or not item.value or not item.other_copies:
                continue
            if item.name in free_text:
                # A company's name or a list of drivers is written differently from one place to the next
                # ("AMV", "AMV Assurance"), and a number is printed in several forms ("0,50", "50 %"): seen on
                # the first live readings. Only codes and dates must say the same thing; names against the
                # machine-readable lines have their own checks.
                continue
            compared.append(item.name)
            if any(not same_value(copy, item.value, pivot) for copy in item.other_copies if copy and copy.strip()):
                differing.append(item.name)
        if differing:
            names = ", ".join(f"« {label(context, name)} »" for name in differing)
            return self.result("fail", f"Les exemplaires d'une même valeur ne concordent pas : {names}.",
                               fields=differing, clues=[f"valeur imprimée deux fois différemment : {label(context, n)}" for n in differing])
        if not compared:
            return self.result("cannot_tell", "Aucun numéro, code ou date imprimé plusieurs fois à comparer.")
        return self.result("pass", f"Les valeurs imprimées plusieurs fois concordent ({len(compared)} champ(s)).",
                           fields=compared)
