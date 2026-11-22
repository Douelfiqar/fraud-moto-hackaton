"""Paper residence documents: the foreigner number, and how long an asylum attestation runs.

foreigner_number: an asylum attestation and an attestation of extended processing print
the holder's number in the national file of foreigners, which has ten digits. A number
of another length, on every copy printed on the document, fails.
asylum_validity: an asylum attestation is issued for one month, then renewed for periods
of ten months at most (six in the fast procedure). One that runs longer from its date of
issue to its end of validity fails. This compares two dates of the document itself: it
does not depend on the day of the check.
prolongation_validity: an attestation of extended processing is valid three months at
most (article R. 431-15-1 of the code on the entry and stay of foreigners). The article
covers that attestation only. A paper filed under this kind but printed under another
title (a "récépissé", for which the code sets no such maximum) is not judged by this
rule: the document's own title decides, as the reading reports it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, doc_type, element_state, parse_date, value

FOREIGNER_NUMBER_DIGITS = 10
ASYLUM_MAX_MONTHS = 10
#: Article R. 431-15-1 of the Code de l'entrée et du séjour des étrangers et du droit d'asile: the attestation
#: of extended processing has a validity that "ne peut être supérieure à trois mois" (renewed, never lengthened).
PROLONGATION_MAX_MONTHS = 3
#: The attestation's own title, an element the reading reports as present or absent
#: (config/doc_types/attestation_prolongation.toml). The three-month maximum belongs to this attestation:
#: the rule judges only a paper that carries this title.
PROLONGATION_TITLE = "Mention ATTESTATION DE PROLONGATION D'INSTRUCTION"
_MARGIN = timedelta(days=5)  # a renewal dated a few days before the previous one ends
NUMBERED_TYPES = frozenset({"attestation_asile", "attestation_prolongation"})


def add_months(day: date, months: int) -> date:
    """The same day ``months`` months later (the last day of the month when that day does not exist)."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    for last in (day.day, 30, 29, 28):
        try:
            return date(year, month + 1, last)
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


@register
class ForeignerNumber(ReadingCheck):
    name = "foreigner_number"
    source = "general"
    description = "Asylum and extended-processing attestations: the foreigner number has ten digits."
    families = frozenset({"identity"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name in NUMBERED_TYPES
                and value(context, "numero_etranger")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        item = context.reading.field("numero_etranger")
        copies = [item.value, *item.other_copies]
        lengths = sorted({len(re.sub(r"\D", "", copy)) for copy in copies if copy} - {0})
        fields = ["numero_etranger"]
        if not lengths:
            return self.result("cannot_tell", "Numéro d'étranger sans chiffre lisible.", fields=fields)
        if FOREIGNER_NUMBER_DIGITS in lengths:
            return self.result("pass", f"Le numéro d'étranger compte {FOREIGNER_NUMBER_DIGITS} chiffres.", fields=fields)
        return self.result(
            "fail",
            f"Le numéro d'étranger compte {' ou '.join(str(length) for length in lengths)} chiffres : ce numéro en "
            f"compte toujours {FOREIGNER_NUMBER_DIGITS}.",
            fields=fields,
            clues=[f"numéro d'étranger qui n'a pas {FOREIGNER_NUMBER_DIGITS} chiffres"],
            fraud_type="document fabriqué",
            details={"digits": lengths},
        )


@register
class AsylumValidity(ReadingCheck):
    name = "asylum_validity"
    source = "general"
    description = "Asylum attestation: it runs ten months at most from its date of issue."
    families = frozenset({"identity"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "attestation_asile"
                and value(context, "date_delivrance")[1] == "read" and value(context, "date_expiration")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        today = context.settings.today()
        issued = parse_date(value(context, "date_delivrance")[0], today.year).value
        ends = parse_date(value(context, "date_expiration")[0], today.year + 30).value
        fields = ["date_delivrance", "date_expiration"]
        if issued is None or ends is None:
            return self.result("cannot_tell", "Date de délivrance ou de fin de validité illisible.", fields=fields)
        if ends > add_months(issued, ASYLUM_MAX_MONTHS) + _MARGIN:
            return self.result(
                "fail",
                f"L'attestation de demande d'asile court sur plus de {ASYLUM_MAX_MONTHS} mois, de sa délivrance à sa fin "
                f"de validité : elle est délivrée pour un mois, puis renouvelée pour {ASYLUM_MAX_MONTHS} mois au plus.",
                fields=fields,
                clues=[f"attestation de demande d'asile valable plus de {ASYLUM_MAX_MONTHS} mois"],
                fraud_type="date modifiée",
                details={"days": (ends - issued).days},
            )
        return self.result("pass", f"L'attestation court sur {ASYLUM_MAX_MONTHS} mois au plus.", fields=fields)


@register
class ProlongationValidity(ReadingCheck):
    """An attestation of extended processing is valid three months at most (article R. 431-15-1 of the code).

    When the processing lasts longer, a new attestation is issued; none runs a year. One
    that does had its dates typed again to look valid. Two dates of the document itself
    are compared: the rule does not depend on the day of the check.

    The article covers this attestation and no other paper, so the rule judges only a
    document on which the reading saw the attestation's own title. A "récépissé" filed
    under the same kind is left to the other checks and to the person who validates.
    """

    name = "prolongation_validity"
    source = "general"
    description = "Attestation of extended processing: it runs three months at most from its date of issue."
    families = frozenset({"identity"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "attestation_prolongation"
                and element_state(context, PROLONGATION_TITLE) == "present"
                and value(context, "date_delivrance")[1] == "read" and value(context, "date_expiration")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        today = context.settings.today()
        issued = parse_date(value(context, "date_delivrance")[0], today.year).value
        ends = parse_date(value(context, "date_expiration")[0], today.year + 30).value
        fields = ["date_delivrance", "date_expiration"]
        if issued is None or ends is None:
            return self.result("cannot_tell", "Date de délivrance ou de fin de validité illisible.", fields=fields)
        if ends > add_months(issued, PROLONGATION_MAX_MONTHS) + _MARGIN:
            return self.result(
                "fail",
                f"L'attestation de prolongation d'instruction court sur plus de {PROLONGATION_MAX_MONTHS} mois, de sa "
                f"délivrance à sa fin de validité : sa durée ne peut pas dépasser {PROLONGATION_MAX_MONTHS} mois (article "
                "R. 431-15-1 du code de l'entrée et du séjour des étrangers et du droit d'asile).",
                fields=fields,
                clues=[f"attestation de prolongation valable plus de {PROLONGATION_MAX_MONTHS} mois"],
                fraud_type="date modifiée",
                details={"days": (ends - issued).days},
            )
        return self.result("pass", f"L'attestation court sur {PROLONGATION_MAX_MONTHS} mois au plus.", fields=fields)
