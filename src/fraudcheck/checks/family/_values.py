"""Helpers shared by the checks on the reading: field access, dates, numbers, normalisation, misreads.

Step 2 (Read) copies values exactly as printed. Cleaning happens here, in code, the same way
for every document: "12 07 1996" becomes a date, spaces are dropped from an IBAN...
"""

from __future__ import annotations

import calendar
import functools
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar, Literal

from fraudcheck.checks.base import Check, DocumentContext
from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.read.doc_types import DocType, doc_type_for, doc_type_named, load_doc_types

State = Literal["read", "absent", "unreadable", "missing"]
#: The usual single-character misreads, tried before a failure counts.
CONFUSIONS = {"O": "0", "0": "O", "I": "1", "1": "I", "B": "8", "8": "B", "S": "5", "5": "S"}
_MONTHS = {
    "janvier": 1, "janv": 1, "jan": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3, "mar": 3, "avril": 4, "avr": 4,
    "mai": 5, "juin": 6, "juillet": 7, "juil": 7, "aout": 8, "septembre": 9, "sept": 9, "sep": 9, "octobre": 10,
    "oct": 10, "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
}
_ISO = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[\s./-]+(\d{1,2})[\s./-]+(\d{4}|\d{2})\b")
_WORDS = re.compile(r"\b(\d{1,2})(?:er)?\s+([a-z]+)\.?\s+(\d{4})\b")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


class ReadingCheck(Check):
    """A check on the reading of step 2; applies to documents of ``families`` with a usable reading."""

    families: ClassVar[frozenset[str] | None] = None

    def applies(self, context: DocumentContext) -> bool:
        reading = context.reading
        if reading is None or not reading.usable:
            return False
        return self.families is None or context.record.family in self.families


def value(context: DocumentContext, name: str) -> tuple[str | None, State]:
    """A field's value as printed and its state; ``missing`` when the reader did not return it."""
    item = context.reading.field(name) if context.reading else None
    if item is None:
        return None, "missing"
    if item.state == "read" and item.value and item.value.strip():
        return item.value.strip(), "read"
    return None, "unreadable" if item.state == "read" else item.state


@functools.lru_cache(maxsize=8)
def _types(folder: str) -> tuple[DocType, ...]:
    try:
        return tuple(load_doc_types(Path(folder)))
    except ConfigError:
        return ()


def doc_type(context: DocumentContext) -> DocType | None:
    """The document type whose fields were asked, from the reading, else from the nature."""
    types = _types(str(context.settings.doc_types_folder))
    named = doc_type_named(context.reading.doc_type if context.reading else None, types)
    return named or doc_type_for(context.record.nature, types)


def label(context: DocumentContext, name: str) -> str:
    """The French label of a field, for evidence ('Date d'édition du relevé')."""
    kind = doc_type(context)
    spec = next((spec for spec in kind.fields if spec.name == name), None) if kind else None
    return spec.label if spec else name


def normalise(text: str) -> str:
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in text if not unicodedata.combining(ch)).casefold().split())


def compact(text: str) -> str:
    """Upper case, no accents, no spaces: how two copies of a value are compared."""
    return normalise(text).replace(" ", "").upper()


def bare(text: str) -> str:
    """Upper case, no accents, letters and digits only: 'AB-123-CD' and 'AB123CD' are the same value."""
    return re.sub(r"[^A-Z0-9]", "", compact(text))


def _date_keys(text: str, pivot_year: int) -> set[tuple[int, int, int]]:
    """Every (day, month, two-digit year) a printed value can stand for.

    A date printed in a field ('06/01/2016') gives one; a run of digits from a
    machine-readable line gives the readings its length allows: YYMMDD or DDMMYY for six
    digits, YYYYMMDD or DDMMYYYY for eight.
    """
    keys = set()
    parsed = parse_date(text, pivot_year)
    if parsed.value is not None:
        keys.add((parsed.value.day, parsed.value.month, parsed.value.year % 100))
    digits = bare(text)
    if digits.isdigit() and len(digits) == 7:
        digits = digits[:6]  # YYMMDD followed by its check digit, as machine-readable lines print it
    if digits.isdigit() and len(digits) in (6, 8):
        if len(digits) == 6:
            readings = [(digits[4:6], digits[2:4], digits[0:2]), (digits[0:2], digits[2:4], digits[4:6])]
        else:
            readings = [(digits[6:8], digits[4:6], digits[2:4]), (digits[0:2], digits[2:4], digits[6:8])]
        for day, month, year in readings:
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                keys.add((int(day), int(month), int(year)))
    return keys


def _unconfused(text: str) -> str:
    """Letters the reader confuses with digits, turned into those digits: two copies differing only so are one value."""
    return text.translate(str.maketrans({"O": "0", "I": "1", "B": "8", "S": "5"}))


def same_value(one: str, two: str, pivot_year: int) -> bool:
    """Do two copies of a value printed on the same document say the same thing?

    A document prints one value in several forms: a plate with and without hyphens, a date
    as '06/01/2016' and as '160106' in the machine-readable line, first names separated
    by commas or by '<', a birthplace with and without its department number, a name cut
    short in the machine-readable line. Only a real contradiction counts as a difference.
    """
    first, second = bare(one), bare(two)
    if not first or not second or first == second:
        return True
    dates_one, dates_two = _date_keys(one, pivot_year), _date_keys(two, pivot_year)
    if dates_one and dates_two:
        return bool(dates_one & dates_two)
    first, second = _unconfused(first), _unconfused(second)
    if first == second:
        return True  # the same value once the usual misreads (O/0, I/1, B/8, S/5) are set aside
    short, long_ = sorted((first, second), key=len)
    if len(short) >= 3 and long_.startswith(short):
        return True  # one copy is cut short, or carries one more piece (a check digit, a department number)
    if len(short) >= 8 and long_.endswith(short):
        return True  # the bank, branch, account and key printed apart are the end of the IBAN
    words_one = set(re.findall(r"[A-Z0-9]+", normalise(one).upper()))
    words_two = set(re.findall(r"[A-Z0-9]+", normalise(two).upper()))
    fewer, more = sorted((words_one, words_two), key=len)
    return bool(fewer) and fewer <= more and any(len(word) >= 3 for word in fewer)


@dataclass(frozen=True)
class ParsedDate:
    value: date | None
    impossible: bool = False


def parse_date(text: str | None, pivot_year: int) -> ParsedDate:
    """A printed date ('12 07 1996', '12.07.96', '1996-07-12', '12 juillet 1996') as a date.

    ``impossible`` is set for a date that does not exist (31 February, month 13);
    ``value`` is ``None`` when the text is not a date at all. Two-digit years are read
    in the century that keeps them at or before ``pivot_year``.
    """
    if not text:
        return ParsedDate(None)
    clean = normalise(text)
    if match := _ISO.search(clean):
        year, month, day = (int(part) for part in match.groups())
    elif match := _NUMERIC.search(clean):
        day, month, year = (int(part) for part in match.groups())
        if len(match.group(3)) == 2:
            year += 2000 if 2000 + year <= pivot_year else 1900
    elif (match := _WORDS.search(clean)) and match.group(2).rstrip(".") in _MONTHS:
        day, month, year = int(match.group(1)), _MONTHS[match.group(2).rstrip(".")], int(match.group(3))
    else:
        return ParsedDate(None)
    if year < 1:
        return ParsedDate(None)  # a placeholder ("00/00/0000"), not a date
    if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]:
        return ParsedDate(None, impossible=True)
    return ParsedDate(date(year, month, day))


_DOUBLED_SEPARATOR = re.compile(
    r"(?<![\d./-])\d{1,2}(?:[./-]{2,}\d{1,2}[./-]+|[./-]+\d{1,2}[./-]{2,})\d{2,4}(?![\d./-])"
)


def malformed_date(text: str | None) -> bool:
    """True for a date printed with one separator too many ('09./09/2026'): no system prints that.

    Only inside one date written without spaces: the dashes between the two dates of a period
    ("15/03/2019 -- 14/03/2020") are not a doubled separator.
    """
    return bool(text and _DOUBLED_SEPARATOR.search(text))


def parse_number(text: str | None) -> float | None:
    """The first number in a printed value ('124,7 cm3' gives 124.7)."""
    if not text:
        return None
    # "1 198 cm3" is 1198: a space after a number standing on its own separates thousands; "P.1 125" stays 125
    cleaned = re.sub(r"\b[A-Za-z]\.\d+(?:\.\d+)?\s", " ", text)  # a field code before the value ("P.1 125")
    cleaned = re.sub(r"(?<![\w.,])(\d{1,3})[   ](?=\d{3}(?!\d))", r"\1", cleaned)
    match = _NUMBER.search(cleaned)
    return float(match.group().replace(",", ".")) if match else None


def misread_variants(text: str) -> Iterator[tuple[str, int, str, str]]:
    """Every version of ``text`` with one usual misread undone: (variant, position, read, meant)."""
    for position, char in enumerate(text):
        if char in CONFUSIONS:
            meant = CONFUSIONS[char]
            yield text[:position] + meant + text[position + 1 :], position, char, meant


def passes_after_misread(text: str, test: Callable[[str], bool]) -> tuple[int, str, str] | None:
    """Where a single usual misread makes ``test`` pass: (position, read as, meant), or ``None``."""
    for variant, position, read_as, meant in misread_variants(text):
        if test(variant):
            return position, read_as, meant
    return None


def misread_note(found: tuple[int, str, str]) -> str:
    position, read_as, meant = found
    return f" (en lisant le caractère n° {position + 1} « {read_as} » comme « {meant} », confusion de lecture courante)"


def element_state(context: DocumentContext, name: str) -> str:
    """The reading's state for an expected element: present, absent, unreadable, or missing when not returned."""
    answer = context.reading.answer if context.reading else None
    item = next((element for element in answer.elements if element.name == name), None) if answer else None
    return item.state if item else "missing"


def years_between(earlier: date, later: date) -> float:
    """Whole years from ``earlier`` to ``later``, counting birthdays exactly."""
    years = later.year - earlier.year - ((later.month, later.day) < (earlier.month, earlier.day))
    return years
