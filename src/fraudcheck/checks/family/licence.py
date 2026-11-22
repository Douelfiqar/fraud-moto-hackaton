"""Licence: category A judged by the date it was obtained.

Rules (French law, as summarised in the team's brief and APRIL's licence fiche):
- since 3 June 2016, A needs two years of A2 first;
- from 19 January 2013 to 2 June 2016, A was open directly at 24, or after two years of A2;
- before 19 January 2013, A was open from 18.
A rule that needs a date the document does not show (the birth date is not on a
licence's back) gives "cannot tell", never "fail".
"""

from __future__ import annotations

from datetime import date

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, parse_date, value, years_between
from fraudcheck.checks.family.licence_rules import french

PROGRESSIVE_ONLY_FROM = date(2016, 6, 3)
DIRECT_AT_24_FROM = date(2013, 1, 19)
A2_YEARS = 2
DIRECT_AGE = 24
ADULT_AGE = 18


@register
class LicenceCategories(ReadingCheck):
    name = "licence_categories"
    source = "general"
    description = "Licence: category A is consistent with A2 and age at the date A was obtained."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        # French rules (A after two years of A2, or directly at 24): a licence of another country follows its own
        return (super().applies(context) and french(context)
                and value(context, "date_categorie_A")[1] in ("read", "unreadable"))

    def run(self, context: DocumentContext) -> CheckResult:
        pivot = context.settings.today().year
        fields = ["date_categorie_A"]
        a_text, a_state = value(context, "date_categorie_A")
        got_a = parse_date(a_text, pivot)
        if a_state != "read" or got_a.value is None:
            return self.result("cannot_tell", "Date d'obtention de la catégorie A illisible.", fields=fields)
        a2_text, a2_state = value(context, "date_categorie_A2")
        got_a2 = parse_date(a2_text, pivot).value if a2_state == "read" else None
        birth = parse_date(value(context, "date_naissance")[0], pivot).value
        two_years_of_a2 = got_a2 is not None and years_between(got_a2, got_a.value) >= A2_YEARS
        fields += ["date_categorie_A2"] + (["date_naissance"] if birth else [])

        if got_a.value >= PROGRESSIVE_ONLY_FROM:
            if a2_state == "absent":
                return self._fail("catégorie A obtenue après le 3 juin 2016 sans catégorie A2", fields)
            if got_a2 is None:
                return self.result("cannot_tell", "Catégorie A obtenue après le 3 juin 2016 : la date de A2 est illisible.",
                                   fields=fields)
            if not two_years_of_a2:
                return self._fail("catégorie A obtenue après le 3 juin 2016 moins de deux ans après la catégorie A2", fields)
            return self.result("pass", "Catégorie A obtenue après deux ans de catégorie A2, comme l'exige la règle depuis 2016.",
                               fields=fields)
        if got_a.value >= DIRECT_AT_24_FROM:
            if two_years_of_a2 or (birth and years_between(birth, got_a.value) >= DIRECT_AGE):
                return self.result("pass", "Catégorie A obtenue entre 2013 et 2016 après deux ans de A2 ou à 24 ans au moins.",
                                   fields=fields)
            if birth and got_a2 is not None:
                return self._fail("catégorie A obtenue entre 2013 et 2016 avant 24 ans et sans deux ans de A2", fields)
            return self.result("cannot_tell", "Catégorie A obtenue entre 2013 et 2016 : l'âge ou la date de A2 manque "
                               "pour juger (la date de naissance n'est pas au verso du permis).", fields=fields)
        if birth is None:
            return self.result("cannot_tell", "Catégorie A obtenue avant 2013 : la date de naissance manque pour juger.",
                               fields=fields)
        if years_between(birth, got_a.value) < ADULT_AGE:
            return self._fail("catégorie A obtenue avant 18 ans", fields)
        return self.result("pass", "Catégorie A obtenue avant 2013, à 18 ans au moins.", fields=fields)

    def _fail(self, what: str, fields: list[str]) -> CheckResult:
        return self.result("fail", f"Incohérence de catégorie : {what}.", fields=fields, clues=[what])
