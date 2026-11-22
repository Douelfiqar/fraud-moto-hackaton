"""Licence: the holder's minimum age, and the dates of the card model of 2013.

licence_min_age: on a document that shows the birth date, every category was obtained
at or above its minimum age (Code de la route): AM 14, A1 and B1 16, A2 18, A 18
before 19 January 2013 and 20 after (two years of A2 first), B 17 (from 2024; earlier,
17 and a half with early training). On a recto, which shows the card's date of issue
and the list of categories but not each category's date, the holder had every listed
category when the card was issued, so the age at issue is at least the highest of the
lowest ages of those categories. Foreign licences follow other laws: not judged.
licence_card_dates: the card model was issued from 16 September 2013, and a card for
motorbikes and cars is valid 15 years (directive 2006/126/CE, article 7). A shorter
validity exists only up to 5 years (medical limit, heavy categories): a validity longer
than 15 years fails, and so does one between the two, which no rule gives.
licence_front_categories: category A gives the right to ride what A2 and A1 cover, and
category B the heavy quadricycles of B1 (Code de la route, articles R. 221-4 to R. 221-8),
and the card of 2013 prints on its front every category its holder has, those included
with the others: A is listed with A2, B with B1. A front that lists A without A2, or B
without B1, was not printed by the issuer. (Measured on the documents at hand: the cards
known to be genuine all follow it.)
foreign_licence_number: a country gives its licence numbers one length (item 5 of the
European model). The lengths are a list (config/licence_numbers.toml) that holds only
the countries whose numbering was verified: Italy, ten characters; Spain, nine (the
holder's identity number). A licence of any other country, or a back, which does not
name the country, is not judged. Covering a country is one entry in the list, no code.
"""

from __future__ import annotations

import re
import tomllib
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import (
    ReadingCheck, doc_type, label, normalise, parse_date, value, years_between,
)

A2_CREATED = date(2013, 1, 19)  # A2 created; A from 20 through two years of A2
CARD_MODEL_FROM = date(2013, 9, 16)  # first French licences in the card format
#: Lowest age for each category whatever the date (A at 18 before 2013, B at 17 since 2024).
LOWEST_AGE = {"AM": 14, "A1": 16, "B1": 16, "A2": 18, "A": 18, "B": 17}
ANY_LICENCE_AGE = 14  # no French licence is issued younger (AM, formerly BSR, at 14)
SHORT_VALIDITY_YEARS = 5  # longest validity of a card limited for medical reasons or for heavy categories
#: On a card's front, the first category is never listed without the second, which it includes.
LISTED_TOGETHER = (("A", "A2"), ("B", "B1"))
_CATEGORY = re.compile(r"(?<![A-Z0-9])(AM|A1|A2|B1|BE|C1E|D1E|C1|D1|CE|DE|A|B|C|D)(?![A-Z0-9])")


def minimum_age(category: str, obtained: date) -> int:
    if category == "A":
        return 20 if obtained >= A2_CREATED else 18
    return LOWEST_AGE[category]


def categories(text: str | None) -> list[str]:
    return list(dict.fromkeys(_CATEGORY.findall((text or "").upper())))


def french(context: DocumentContext) -> bool:
    kind = doc_type(context)
    return kind is not None and kind.name != "permis_etranger"


@register
class LicenceMinimumAge(ReadingCheck):
    name = "licence_min_age"
    source = "general"
    description = "Licence: every category was obtained at or above its minimum age (French law)."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and french(context) and value(context, "date_naissance")[1] == "read"

    def run(self, context: DocumentContext) -> CheckResult:
        pivot = context.settings.today().year
        birth = parse_date(value(context, "date_naissance")[0], pivot).value
        if birth is None:
            return self.result("cannot_tell", "Date de naissance illisible.", fields=["date_naissance"])
        judged, young = [], []
        for category in LOWEST_AGE:
            text, state = value(context, f"date_categorie_{category}")
            obtained = parse_date(text, pivot).value if state == "read" else None
            if obtained is not None:
                judged.append(f"date_categorie_{category}")
                if years_between(birth, obtained) < minimum_age(category, obtained):
                    young.append((f"date_categorie_{category}", f"catégorie {category} obtenue avant "
                                  f"{minimum_age(category, obtained)} ans"))
        issued_text, issued_state = value(context, "date_delivrance")
        issued = parse_date(issued_text, pivot).value if issued_state == "read" else None
        if issued is not None:
            listed = [name for name in categories(value(context, "categories")[0]) if name in LOWEST_AGE]
            needed = max((LOWEST_AGE[name] for name in listed), default=ANY_LICENCE_AGE)
            judged.append("date_delivrance")
            if years_between(birth, issued) < needed:
                young.append(("date_delivrance", f"permis délivré avant {needed} ans, âge minimum "
                              + ("des catégories qu'il porte" if listed else "de tout permis")))
        fields = ["date_naissance", *judged]
        if young:
            return self.result("fail", "Âge impossible : " + " ; ".join(what for _, what in young) + ".",
                               fields=["date_naissance", *(field for field, _ in young)],
                               clues=["âge trop jeune pour la catégorie"], fraud_type="date modifiée")
        if not judged:
            return self.result("cannot_tell", "Aucune date d'obtention ou de délivrance lisible à comparer à la date de "
                               "naissance.", fields=fields)
        return self.result("pass", f"Âge suffisant à chacune des {len(judged)} date(s) comparée(s) à la date de "
                           "naissance.", fields=fields)


@register
class LicenceCardDates(ReadingCheck):
    name = "licence_card_dates"
    source = "general"
    description = "Licence card (model of 2013): issued from 16 September 2013 and valid at most 15 years."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "permis_nouveau_recto"
                and value(context, "date_delivrance")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        settings = context.settings
        issued = parse_date(value(context, "date_delivrance")[0], settings.today().year).value
        if issued is None:
            return self.result("cannot_tell", "Date de délivrance (4a) illisible.", fields=["date_delivrance"])
        if issued < CARD_MODEL_FROM:
            return self.result("fail", f"{label(context, 'date_delivrance')} antérieure au 16 septembre 2013, date des "
                               "premiers permis au format carte.", fields=["date_delivrance"],
                               clues=["permis au format carte daté d'avant 2013"], fraud_type="date modifiée")
        ends_text, ends_state = value(context, "date_expiration")
        ends = parse_date(ends_text, settings.today().year + 30).value if ends_state == "read" else None
        if ends is None:
            return self.result("pass", "Date de délivrance possible pour un permis au format carte ; fin de validité "
                               "illisible.", fields=["date_delivrance", "date_expiration"])
        years = settings.licence_validity_years
        try:
            longest = issued.replace(year=issued.year + years)
        except ValueError:  # 29 February
            longest = issued.replace(year=issued.year + years, day=28)
        if ends > longest + timedelta(days=settings.licence_validity_tolerance_days):
            return self.result("fail", f"Validité du permis plus longue que les {years} ans permis (4a à 4b).",
                               fields=["date_delivrance", "date_expiration"],
                               clues=[f"validité de plus de {years} ans"], fraud_type="date modifiée",
                               details={"years": round((ends - issued).days / 365.25, 2)})
        margin = timedelta(days=settings.licence_validity_tolerance_days)
        try:
            short = issued.replace(year=issued.year + SHORT_VALIDITY_YEARS)
        except ValueError:  # 29 February
            short = issued.replace(year=issued.year + SHORT_VALIDITY_YEARS, day=28)
        if short + margin < ends < longest - margin:
            return self.result("fail", f"Durée de validité du permis (4a à 4b) qui n'existe pas : un permis au format "
                               f"carte est valable {years} ans, ou {SHORT_VALIDITY_YEARS} ans au plus pour les catégories "
                               "lourdes ou sur avis médical.", fields=["date_delivrance", "date_expiration"],
                               clues=[f"validité ni de {years} ans ni de {SHORT_VALIDITY_YEARS} ans au plus"],
                               fraud_type="document fabriqué",
                               details={"years": round((ends - issued).days / 365.25, 2)})
        return self.result("pass", f"Délivré après le 16 septembre 2013 et valable {years} ans, ou "
                           f"{SHORT_VALIDITY_YEARS} ans au plus.", fields=["date_delivrance", "date_expiration"])


@register
class LicenceFrontCategories(ReadingCheck):
    name = "licence_front_categories"
    source = "general"
    description = "Licence card front: category A is listed with A2 and B with B1, which they include."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "permis_nouveau_recto"
                and value(context, "categories")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        listed = categories(value(context, "categories")[0])
        if not listed:
            return self.result("cannot_tell", "Liste des catégories (rubrique 9) illisible.", fields=["categories"])
        missing = [(holder, included) for holder, included in LISTED_TOGETHER
                   if holder in listed and included not in listed]
        if missing:
            return self.result(
                "fail",
                "Liste des catégories (rubrique 9) incohérente : "
                + " ; ".join(f"catégorie {holder} sans la catégorie {included} qu'elle inclut"
                             for holder, included in missing) + ".",
                fields=["categories"],
                clues=[f"catégorie {holder} sans {included}" for holder, included in missing],
                fraud_type="document fabriqué",
            )
        return self.result("pass", "Les catégories listées au recto vont avec celles qu'elles incluent.",
                           fields=["categories"])


#: Categories of motorbikes and cars: one administrative validity for the licence that carries them
#: (directive 2006/126/CE, article 7, paragraph 2 a).
SAME_VALIDITY = ("AM", "A1", "A2", "A", "B1", "B")


@register
class LicenceSidesAgree(ReadingCheck):
    """The categories listed on the front of a card are the ones dated on its back.

    Field 9 on the front lists the categories held. On the back, column 10 prints the date
    each one was obtained, and a row without a date is a category not held. A category
    listed without a date, or dated without being listed, means one side was changed or
    the two sides do not belong together: a date put in the row of a heavier category is
    how a licence is made to look like one for bigger motorbikes.
    Judged only when both sides are in the same reading, that is a scan showing the front
    and the back. Written and tested on made-up readings: not trusted before it has been
    seen on real ones.
    """

    name = "licence_sides_agree"
    source = "general"
    description = "Licence card scanned on both sides: the categories listed on the front are the ones dated on the back."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "permis_nouveau_recto"
                and value(context, "categories")[1] == "read"
                and any(value(context, f"date_categorie_{category}")[1] == "read" for category in SAME_VALIDITY))

    def run(self, context: DocumentContext) -> CheckResult:
        listed = [category for category in categories(value(context, "categories")[0]) if category in SAME_VALIDITY]
        states = {category: value(context, f"date_categorie_{category}")[1] for category in SAME_VALIDITY}
        fields = ["categories", *(f"date_categorie_{category}" for category in SAME_VALIDITY if states[category] == "read")]
        if not listed:
            return self.result("cannot_tell", "Liste des catégories du recto (rubrique 9) illisible.", fields=fields)
        undated = [category for category in listed if states[category] == "absent"]
        unlisted = [category for category in SAME_VALIDITY if states[category] == "read" and category not in listed]
        if undated or unlisted:
            parts = []
            if undated:
                parts.append(f"catégorie(s) {', '.join(undated)} listée(s) au recto sans date d'obtention au verso")
            if unlisted:
                parts.append(f"catégorie(s) {', '.join(unlisted)} datée(s) au verso sans être listée(s) au recto")
            return self.result(
                "fail",
                "Le recto et le verso du permis ne portent pas les mêmes catégories : " + " ; ".join(parts) + ".",
                fields=fields,
                clues=["catégories du recto différentes de celles datées au verso"],
                fraud_type="catégorie modifiée",
                details={"listed_without_date": undated, "dated_without_listing": unlisted},
            )
        if any(states[category] != "read" for category in listed):
            return self.result("cannot_tell", "Une date d'obtention du verso n'a pas pu être lue pour une catégorie listée "
                               "au recto.", fields=fields)
        return self.result("pass", f"Les {len(listed)} catégories listées au recto ont chacune leur date d'obtention au "
                           "verso, et aucune autre n'y est datée.", fields=fields)


@register
class LicenceCategoryValidity(ReadingCheck):
    """Column 11 of a card of the European model prints the end of validity of each category.

    Motorbike and car categories share one administrative validity, so the dates printed for
    them on one card are the same. A card whose categories end on different dates was put
    together from several cards, or had a date typed again. Known limit: a medical
    restriction can shorten one category alone, which is why the decision gives this check
    a moderate confidence. French cards usually leave the column empty: nothing is judged.
    """

    name = "licence_category_validity"
    source = "general"
    description = "Licence back: the motorbike and car categories of one card end their validity on the same date."
    families = frozenset({"licence"})

    def _dates(self, context: DocumentContext) -> dict[str, date]:
        pivot, found = context.settings.today().year + 30, {}
        for category in SAME_VALIDITY:
            text, state = value(context, f"fin_validite_{category}")
            day = parse_date(text, pivot).value if state == "read" else None
            if day is not None:
                found[category] = day
        return found

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and len(self._dates(context)) >= 2

    def run(self, context: DocumentContext) -> CheckResult:
        dates = self._dates(context)
        fields = [f"fin_validite_{category}" for category in dates]
        different = len(set(dates.values()))
        if different == 1:
            return self.result("pass", f"Les {len(dates)} catégories lues finissent leur validité à la même date "
                               "(colonne 11).", fields=fields)
        return self.result(
            "fail",
            f"Les catégories {', '.join(dates)} d'une même carte portent {different} dates de fin de validité différentes "
            "(colonne 11) : les catégories moto et voiture d'un permis ont une seule validité administrative.",
            fields=fields,
            clues=["dates de fin de validité différentes entre catégories d'une même carte"],
            fraud_type="date modifiée",
            details={"different_dates": different, "categories": list(dates)},
        )


@lru_cache(maxsize=4)
def licence_number_lengths(path: str) -> tuple[tuple[tuple[str, ...], int, str], ...]:
    """For each country of config/licence_numbers.toml: the words that name it, its number length, its name."""
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ()
    return tuple(
        (tuple(normalise(str(word)) for word in country.get("words", [])), int(country["length"]), str(country.get("name", "")))
        for country in data.get("country", []) if "length" in country
    )


def number_length_of_country(context: DocumentContext) -> tuple[int, str] | None:
    """The length the country printed on the licence gives its numbers, with the country's name; None when not listed."""
    printed = normalise(value(context, "pays")[0] or "")
    for words, length, name in licence_number_lengths(str(context.settings.licence_numbers_file)):
        if any(word and word in printed for word in words):
            return length, name
    return None


@register
class ForeignLicenceNumber(ReadingCheck):
    name = "foreign_licence_number"
    source = "general"
    description = ("Foreign licence: the licence number has the length its country gives it (countries listed in "
                   "config/licence_numbers.toml).")
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "permis_etranger"
                and value(context, "numero_permis")[1] == "read" and number_length_of_country(context) is not None)

    def run(self, context: DocumentContext) -> CheckResult:
        expected, country = number_length_of_country(context)
        item = context.reading.field("numero_permis")
        lengths = sorted({len(re.sub(r"[^A-Za-z0-9]", "", copy)) for copy in [item.value, *item.other_copies] if copy} - {0})
        fields = ["numero_permis", "pays"]
        details = {"characters": lengths, "expected": expected, "country": country}
        if not lengths:
            return self.result("cannot_tell", "Numéro du permis sans caractère lisible.", fields=fields)
        if expected in lengths:
            return self.result("pass", f"Le numéro du permis compte {expected} caractères, comme tout numéro de permis "
                               "délivré par ce pays.", fields=fields, details=details)
        return self.result(
            "fail",
            f"Le numéro du permis (rubrique 5) compte {' ou '.join(str(length) for length in lengths)} caractères : un "
            f"numéro de permis délivré par ce pays en compte toujours {expected}.",
            fields=fields,
            clues=[f"numéro de permis qui n'a pas les {expected} caractères de son pays de délivrance"],
            fraud_type="valeur modifiée",
            details=details,
        )
