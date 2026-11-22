"""A document whose validity date is already past, at the date of the check.

APRIL's sheets judge validity against the day the file is examined: the residence-permit
sheet says the validity date MUST be a future date, and the licence sheet tells staff to
watch the validity dates and the renewal date of the card licences (4b). The organisers
count a document that does not conform to the sheets as a fraud ("constitution non
conforme"), so an expired document fails here.

"Today" is the setting ``reference_date``, or the day of the run.

One printed date is not the real one: French identity cards issued to adults between
2 January 2004 and 31 December 2013 are valid fifteen years, five more than printed
(décret 2013-1188). Without that, a genuine card would be called expired.
"""

from __future__ import annotations

from datetime import date

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, doc_type, parse_date, value, years_between

LONGER_CARDS_FROM, LONGER_CARDS_TO = date(2004, 1, 2), date(2013, 12, 31)
EXTRA_YEARS = 5
ADULT_AGE = 18


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


@register
class ValidityExpired(ReadingCheck):
    name = "validity_expired"
    source = "fiche"
    description = "Identity documents, residence permits and licences: the validity date is already past."
    families = frozenset({"identity", "licence"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "date_expiration")[1] != "missing"

    def run(self, context: DocumentContext) -> CheckResult:
        kind = doc_type(context)
        today = context.settings.today()
        text, state = value(context, "date_expiration")
        parsed = parse_date(text, today.year + 30)  # validity dates may lie in the future
        fields = ["date_expiration"]
        if state != "read" or parsed.value is None:
            return self.result("cannot_tell", "Date de fin de validité absente ou illisible.", fields=fields)
        ends, note = parsed.value, ""
        if kind is not None and kind.name == "piece_identite":
            issued = parse_date(value(context, "date_delivrance")[0], today.year).value
            born = parse_date(value(context, "date_naissance")[0], today.year).value
            if (issued and born and LONGER_CARDS_FROM <= issued <= LONGER_CARDS_TO
                    and years_between(born, issued) >= ADULT_AGE):
                ends = _add_years(ends, EXTRA_YEARS)
                note = (" Carte d'identité délivrée à un adulte entre 2004 et 2013 : sa validité est de quinze ans, "
                        "cinq de plus que la date imprimée.")
        if ends < today:
            return self.result(
                "fail",
                f"La date de fin de validité est dépassée à la date du contrôle ({today.strftime('%d/%m/%Y')}) : la "
                f"fiche pratique demande une date de validité à venir.{note}",
                fields=fields,
                clues=["document expiré à la date du contrôle"],
                fraud_type="document expiré (non conforme)",
            )
        return self.result("pass", f"Le document est en cours de validité à la date du contrôle.{note}", fields=fields)
