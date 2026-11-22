"""Relevé: the European model since 24 July 2025.

releve_new_model: since 24 July 2025 every relevé follows the European model of
regulation (EU) 2024/1855 (APRIL's fiche on the new relevé says the same): sections A to
F, then "Date et signature", items numbered 1 to 24. A relevé edited on or after that
date without them fails. A relevé that carries the model's own two-language wording
("NEANT / NIL") but not all its sections is an incomplete copy of the new model, not an
old one: it is left to a person ("cannot tell").
bonus_malus_licence: the coefficient starts at 1.00 and loses 5 % for each year without
an at-fault claim (annex to article A121-1 of the Code des assurances), so a driver
cannot hold a coefficient lower than the time since the licence allows. APRIL's sheet
asks for it: "attention à la cohérence de la date du permis de conduire". The licence
date is the earliest one printed on the relevé.
The coefficient follows the driver from one vehicle to another (same annex, article 10):
a rider with a recent motorcycle licence lawfully brings the coefficient earned in a car.
So the rule speaks only when the relevé says the vehicle is a car. A relevé of a
two-wheeler, and one that does not say what the vehicle is, are not judged ("cannot
tell"): a wrong flag costs more than a missed one.
What kind of vehicle a relevé describes is read from four signs, none of them a list of
models: the category letter of the European model (A car, B motorcycle, D moped), the
maker code that starts the vehicle identification number, a word that names the kind
(moto, scooter, cm3; voiture, automobile), and the make when its maker builds one kind
only (config/vehicle_makers.toml). A maker of both cars and two-wheelers says nothing
by its name alone.
Known limits, which is why the decision gives this check a moderate confidence: a
vehicle insured for business rounds loses 7 % a year instead of 5 %, and a driver may
bring to a car a coefficient first earned on a motorcycle. Both are rare.
releve_vehicle_category: item 9 of the European model classes the vehicle by a letter,
A for a car, B for a motorcycle, D for a moped (regulation (EU) 2024/1855), and item 11
prints its identification number, whose first three characters name the maker (ISO
3780). A relevé that classes as a car the vehicle of a maker of two-wheelers, or the
reverse, contradicts itself: the vehicle was typed over another vehicle's relevé. The maker
codes are a list (config/vehicle_makers.toml) that holds only makers of one kind of
vehicle; any other maker, a relevé without the letter or without the number, is not
judged. It answers from the document what a look-up of the plate in the registration
files would answer, without sending anything out.
"""

from __future__ import annotations

import math
import re
import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, element_state, normalise, parse_date, parse_number, value

NEW_MODEL_FROM = date(2025, 7, 24)
SECTIONS = ("Sections A à F et « Date et signature » du modèle européen (relevés édités depuis le 24 juillet 2025)")
# ^ an element of config/doc_types/releve.toml: the reading says whether the document shows it
#: The European model prints empty items in French and English.
_TWO_LANGUAGES = re.compile(r"\bn[ée]ant\s*/\s*nil\b", re.IGNORECASE)
_WORDED_FIELDS = ("sinistres", "motif_resiliation", "date_resiliation", "situation_contrat")
YEARLY_REDUCTION, LOWEST_COEFFICIENT, HIGHEST_COEFFICIENT = 0.95, 0.50, 3.50
#: A first insurance period of nine months already counts as a year (same annex, article 8).
FIRST_PERIOD_YEARS = 0.75
#: Item 9 of the European model: A is a car; B a motorcycle and D a moped (regulation (EU) 2024/1855).
CAR_CATEGORY, TWO_WHEELER_CATEGORIES = "A", frozenset("BD")
#: The category letters come first in the vehicle's description ("A", "A : voiture", "A-F"), before a separator or
#: the make. A letter followed by digits is the start of an old plate, not a category.
_CATEGORY_FIRST = re.compile(r"^\s*([A-G](?:\s*[-,]\s*[A-G])*)(?=\s*[:/]|\s+[^\W\d_]{2,}|\s*$)")
_IDENTIFICATION_NUMBER = re.compile(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])")
#: Words that name the kind of vehicle, whatever its make (matched without accents or capitals).
_TWO_WHEELER_WORDS = re.compile(r"\b(?:motos?|motocycl\w*|scooters?|cyclo\w*|2 ?roues|deux[- ]roues|cm3)\b")
_CAR_WORDS = re.compile(r"\b(?:voitures?|automobiles?|auto)\b")
#: A plate printed inside the description is taken out before words are looked for: its letters are not a make.
_PLATE = re.compile(r"\b[A-Z]{2}[- ]?\d{3}[- ]?[A-Z]{2}\b|\b\d{1,4} ?[A-Z]{1,3} ?\d{2,3}\b")
_FULL_DATE = re.compile(r"\b\d{1,2}[./ -]\d{1,2}[./ -]\d{2,4}\b")
_MONTH_YEAR = re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{4})\b")


def european_wording(context: DocumentContext) -> bool:
    """True when the reading holds the new model's two-language wording for an empty item."""
    return any(_TWO_LANGUAGES.search(value(context, name)[0] or "") for name in _WORDED_FIELDS)


def lowest_coefficient(years: int) -> float:
    """The lowest coefficient reachable after ``years`` yearly reductions from 1.00, each rounded down to the hundredth."""
    coefficient = 1.0
    for _ in range(max(0, years)):
        coefficient = max(LOWEST_COEFFICIENT, math.floor(coefficient * YEARLY_REDUCTION * 100 + 1e-9) / 100)
    return coefficient


def earliest_date(text: str | None, pivot_year: int) -> date | None:
    """The earliest date printed in a value that may hold several ('12/06/2024 (A1) 04/09/2011 (B)', '04/1997')."""
    found = [parsed for piece in _FULL_DATE.findall(text or "") if (parsed := parse_date(piece, pivot_year).value)]
    for month, year in _MONTH_YEAR.findall(_FULL_DATE.sub(" ", text or "")):
        if 1 <= int(month) <= 12:
            found.append(date(int(year), int(month), 1))
    return min(found) if found else None


@register
class ReleveNewModel(ReadingCheck):
    name = "releve_new_model"
    source = "fiche"
    description = "Relevé: one edited since 24 July 2025 has sections A to F and 'Date et signature' (EU 2024/1855)."
    families = frozenset({"releve"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "date_edition")[1] == "read"

    def run(self, context: DocumentContext) -> CheckResult:
        edited = parse_date(value(context, "date_edition")[0], context.settings.today().year).value
        fields = ["date_edition"]
        if edited is None:
            return self.result("cannot_tell", "Date d'édition du relevé illisible.", fields=fields)
        if edited < NEW_MODEL_FROM:
            return self.result("pass", "Relevé édité avant le 24 juillet 2025 : l'ancien modèle était admis.",
                               fields=fields)
        state = element_state(context, SECTIONS)
        if state == "present":
            return self.result("pass", "Relevé édité depuis le 24 juillet 2025, avec les sections A à F et « Date et "
                               "signature » du modèle européen.", fields=fields)
        if state == "absent" and european_wording(context):
            return self.result("cannot_tell", "Relevé édité depuis le 24 juillet 2025 qui porte les mentions bilingues "
                               "du modèle européen mais pas toutes ses sections : copie incomplète du nouveau modèle, à "
                               "faire compléter.", fields=fields)
        if state == "absent":
            return self.result("fail", "Relevé édité depuis le 24 juillet 2025 sans les sections A à F et « Date et "
                               "signature » du modèle européen, obligatoire depuis cette date (règlement (UE) "
                               "2024/1855).", fields=fields,
                               clues=["relevé récent à l'ancien modèle"], fraud_type="document fabriqué")
        return self.result("cannot_tell", "Relevé édité depuis le 24 juillet 2025 : la présence des sections du modèle "
                           "européen n'a pas pu être lue.", fields=fields)


@register
class BonusMalusLicence(ReadingCheck):
    name = "bonus_malus_licence"
    source = "fiche"
    description = ("Relevé of a car: the coefficient is not lower than the time since the licence printed on it "
                   "allows (judged only when the relevé says the vehicle is a car).")
    families = frozenset({"releve"})

    def applies(self, context: DocumentContext) -> bool:
        return (super().applies(context) and value(context, "bonus_malus")[1] == "read"
                and value(context, "date_permis")[1] == "read" and value(context, "date_edition")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        pivot = context.settings.today().year
        fields = ["bonus_malus", "date_permis", "date_edition"]
        kind = vehicle_kind(vehicle_description(context)[0], str(context.settings.vehicle_makers_file))
        if kind == "two_wheeler":
            return self.result("cannot_tell", "Relevé d'un deux-roues : le coefficient peut venir d'un permis auto plus "
                               "ancien que le permis moto imprimé, il n'est pas comparé à l'ancienneté du permis.",
                               fields=[*fields, "vehicule"])
        if kind is None:
            return self.result("cannot_tell", "Le relevé ne dit pas que le véhicule est une voiture : le coefficient "
                               "peut venir d'un autre permis que celui imprimé, il n'est pas comparé à l'ancienneté du "
                               "permis.", fields=[*fields, "vehicule"])
        coefficient = parse_number(value(context, "bonus_malus")[0])
        if coefficient is not None and coefficient > HIGHEST_COEFFICIENT:
            coefficient /= 100  # printed as a percentage: 90 for 0.90
        licence = earliest_date(value(context, "date_permis")[0], pivot)
        edited = parse_date(value(context, "date_edition")[0], pivot).value
        if coefficient is None or licence is None or edited is None or edited < licence:
            return self.result("cannot_tell", "Coefficient, date de permis ou date d'édition illisible : l'ancienneté ne "
                               "peut pas être comparée au coefficient.", fields=fields)
        years = int((edited - licence).days / 365.25 + (1 - FIRST_PERIOD_YEARS))
        floor = lowest_coefficient(years)
        if coefficient < floor - 0.005:
            return self.result(
                "fail",
                "Le coefficient de réduction-majoration est plus bas que ce que permet l'ancienneté du permis imprimée "
                "sur le relevé : le coefficient part de 1,00 et ne baisse que de 5 % par année sans sinistre responsable.",
                fields=fields,
                clues=["coefficient bonus-malus trop bas pour l'ancienneté du permis"],
                fraud_type="coefficient modifié",
                details={"years": years, "lowest": floor},
            )
        return self.result("pass", "Le coefficient est possible pour l'ancienneté du permis imprimée sur le relevé.",
                           fields=fields, details={"years": years, "lowest": floor})


@lru_cache(maxsize=4)
def _makers_file(path: str) -> dict:
    """config/vehicle_makers.toml as read; empty when the file cannot be read (the rules then say they cannot tell)."""
    try:
        return tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


@lru_cache(maxsize=4)
def maker_kinds(path: str) -> dict[str, str]:
    """Maker code -> ``two_wheeler`` or ``car``: the makers whose code covers one kind of vehicle only."""
    data = _makers_file(path)
    kinds = {str(code).upper(): "two_wheeler" for code in data.get("two_wheelers", [])}
    kinds.update({str(code).upper(): "car" for code in data.get("cars", [])})
    return kinds


@lru_cache(maxsize=4)
def make_names(path: str) -> dict[str, re.Pattern[str] | None]:
    """For each kind, a pattern of the makes that build that kind only, as a relevé prints them (None: no make listed)."""
    data = _makers_file(path)

    def pattern(names: list) -> re.Pattern[str] | None:
        words = [re.escape(normalise(str(name))).replace(r"\ ", "[ -]") for name in names if str(name).strip()]
        return re.compile(r"\b(?:" + "|".join(words) + r")\b") if words else None

    return {"two_wheeler": pattern(data.get("two_wheeler_makes", [])), "car": pattern(data.get("car_makes", []))}


def printed_categories(text: str | None) -> set[str]:
    """The category letters printed before the make; empty when the description does not start with them."""
    found = _CATEGORY_FIRST.match((text or "").upper())
    return set(re.findall(r"[A-G]", found.group(1))) if found else set()


def maker_code(text: str | None) -> str | None:
    """The first three characters of the vehicle identification number printed in the description, if there is one."""
    for number in _IDENTIFICATION_NUMBER.findall((text or "").upper()):
        if any(char.isdigit() for char in number) and any(char.isalpha() for char in number):
            return number[:3]
    return None


#: Where a relevé's vehicle is read: the category letter and the identification number of the European model when
#: the reader returned them as fields of their own, and the free description, which may hold them too.
_VEHICLE_FIELDS = ("categorie_vehicule", "vehicule", "numero_identification_vehicule")


def vehicle_description(context: DocumentContext) -> tuple[str | None, list[str]]:
    """What the relevé prints about its vehicle, the category first, and the fields it was read from."""
    read = [(name, text) for name in _VEHICLE_FIELDS for text, state in [value(context, name)] if state == "read" and text]
    return " / ".join(text for _, text in read) or None, [name for name, _ in read]


def vehicle_kind(text: str | None, makers_path: str) -> str | None:
    """``two_wheeler``, ``car``, or None when the description does not say.

    Four signs are read: the category letter, the maker code of the identification number, a word that names the
    kind, and the make when its maker builds one kind only. A sign of a two-wheeler wins over a sign of a car: the
    rules that use the answer then leave the document to a person instead of judging it as a car.
    """
    if not text or not text.strip():
        return None
    letters = printed_categories(text)
    by_code = maker_kinds(makers_path).get(maker_code(text) or "")
    plain = normalise(_PLATE.sub(" ", text.upper()))
    names = make_names(makers_path)

    def named(kind: str) -> bool:
        return names[kind] is not None and names[kind].search(plain) is not None

    if letters & TWO_WHEELER_CATEGORIES or by_code == "two_wheeler" or _TWO_WHEELER_WORDS.search(plain) or named("two_wheeler"):
        return "two_wheeler"
    if CAR_CATEGORY in letters or by_code == "car" or _CAR_WORDS.search(plain) or named("car"):
        return "car"
    return None


@register
class ReleveVehicleCategory(ReadingCheck):
    name = "releve_vehicle_category"
    source = "general"
    description = ("Relevé: the vehicle's category letter (A car, B motorcycle) agrees with the maker named by the "
                   "identification number printed beside it.")
    families = frozenset({"releve"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and vehicle_description(context)[0] is not None

    def run(self, context: DocumentContext) -> CheckResult:
        text, fields = vehicle_description(context)
        letters, code = printed_categories(text), maker_code(text)
        if not letters or code is None:
            return self.result("cannot_tell", "Le relevé n'imprime pas à la fois la catégorie du véhicule (rubrique 9) et "
                               "son numéro d'identification (rubrique 11) : rien à comparer.", fields=fields)
        kind = maker_kinds(str(context.settings.vehicle_makers_file)).get(code)
        if kind is None:
            return self.result("cannot_tell", "Le constructeur que désigne le numéro d'identification du véhicule "
                               "fabrique plusieurs genres de véhicules ou n'est pas répertorié : la catégorie n'est pas "
                               "jugée.", fields=fields)
        printed = ", ".join(sorted(letters))
        details = {"categories": sorted(letters), "maker_kind": kind}
        contradiction = None
        if kind == "two_wheeler" and letters == {CAR_CATEGORY}:
            contradiction = ("voiture", "un constructeur de deux-roues")
        elif kind == "car" and letters <= TWO_WHEELER_CATEGORIES:
            contradiction = ("deux-roues", "un constructeur de voitures")
        if contradiction is None:
            return self.result("pass", f"La catégorie {printed} du véhicule (rubrique 9) s'accorde avec le constructeur "
                               "que désigne son numéro d'identification (rubrique 11).", fields=fields, details=details)
        return self.result(
            "fail",
            f"La rubrique 9 classe le véhicule en catégorie {printed} ({contradiction[0]}), alors que son numéro "
            f"d'identification (rubrique 11) commence par le code d'{contradiction[1]} : sur le modèle européen du "
            "relevé, A désigne une voiture, B un motocycle et D un cyclomoteur (règlement d'exécution (UE) 2024/1855).",
            fields=fields,
            clues=["catégorie de véhicule contredite par le numéro d'identification du véhicule"],
            fraud_type="valeur modifiée",
            details=details,
        )


def months_before(day: date, months: int) -> date:
    """The same day ``months`` months earlier (the last day of the month when that day does not exist)."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    for last in (day.day, 30, 29, 28):
        try:
            return date(year, month + 1, last)
        except ValueError:
            continue
    raise ValueError("unreachable")  # pragma: no cover


@register
class ReleveTooOld(ReadingCheck):
    """APRIL's sheet: "Date d'édition du RI : doit dater de moins de 3 mois selon la date d'Affaire nouvelle".

    The date of the new contract is not on the document, so the age is counted to the date of
    the check (``reference_date``, or the day of the run). A relevé dated after that day fails too.
    """

    name = "releve_too_old"
    source = "fiche"
    description = "Relevé: edited less than three months before the date of the check (APRIL's sheet), and not after it."
    families = frozenset({"releve"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "date_edition")[1] != "missing"

    def run(self, context: DocumentContext) -> CheckResult:
        today, months = context.settings.today(), context.settings.releve_max_age_months
        text, state = value(context, "date_edition")
        edited = parse_date(text, today.year).value if state == "read" else None
        fields = ["date_edition"]
        if edited is None:
            return self.result("cannot_tell", "Date d'édition du relevé absente ou illisible.", fields=fields)
        if edited > today:
            return self.result("fail", "Le relevé est daté après la date du contrôle "
                               f"({today.strftime('%d/%m/%Y')}).", fields=fields,
                               clues=["relevé daté dans le futur"], fraud_type="date impossible")
        if edited < months_before(today, months):
            return self.result("fail", f"Le relevé a été édité plus de {months} mois avant la date du contrôle "
                               f"({today.strftime('%d/%m/%Y')}) : la fiche pratique demande un relevé de moins de "
                               f"{months} mois.", fields=fields,
                               clues=[f"relevé de plus de {months} mois"], fraud_type="document périmé (non conforme)",
                               details={"age_days": (today - edited).days})
        return self.result("pass", f"Relevé édité moins de {months} mois avant la date du contrôle.", fields=fields,
                           details={"age_days": (today - edited).days})
