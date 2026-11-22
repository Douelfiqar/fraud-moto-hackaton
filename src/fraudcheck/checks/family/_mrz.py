"""Machine-readable lines (MRZ): layouts, check digits and the values they carry.

Layouts (ICAO Doc 9303, and the French identity card of 1995):
- TD1, three lines of 30 (identity card since 2021, residence permit);
- TD2, two lines of 36;
- TD3, two lines of 44 (passport);
- CNI 1995, two lines of 36 starting "IDFRA": surname, then card number, given names, birth date.
Check digits use weights 7, 3, 1 over the characters' values (digits as such, A=10 ... Z=35,
filler '<' = 0), modulo 10. The reading may add spaces or line breaks: they are removed,
and the layout is found from the total length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LENGTHS = {90: ("TD1", 30), 72: ("TD2", 36), 88: ("TD3", 44)}
#: In a field that holds only digits, these letters can only be misreads of the digit.
DIGIT_FOR = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8"}


def char_value(char: str) -> int:
    if char.isdigit():
        return int(char)
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    return 0  # the filler '<'


def check_digit(data: str) -> int:
    return sum(char_value(char) * (7, 3, 1)[position % 3] for position, char in enumerate(data)) % 10


def digits_only(text: str) -> str:
    return "".join(DIGIT_FOR.get(char, char) for char in text)


@dataclass(frozen=True)
class Checked:
    """One check digit: what it covers, the digit printed, and whether they agree."""

    name: str
    data: str
    printed: str

    @property
    def valid(self) -> bool:
        if self.printed == "<":
            return set(self.data) <= {"<"}  # an empty optional field may carry a filler instead of a digit
        return self.printed.isdigit() and check_digit(self.data) == int(self.printed)


@dataclass
class Mrz:
    layout: str
    lines: list[str]
    number: str = ""
    birth: str = ""  # YYMMDD
    expiry: str = ""  # YYMMDD, empty when the layout has none
    surname: str = ""
    given_names: str = ""
    checks: list[Checked] = field(default_factory=list)

    @property
    def failed(self) -> list[Checked]:
        return [item for item in self.checks if not item.valid]


def clean(text: str) -> str:
    """Upper case, without spaces or line breaks; '«' (two fillers read as one sign) back to '<<'."""
    return re.sub(r"\s+", "", text.upper().replace("«", "<<").replace("‹", "<"))


def names(zone: str) -> tuple[str, str]:
    """(surname, given names) from a names zone: 'SURNAME<<GIVEN<NAMES<<<' gives ('SURNAME', 'GIVEN NAMES')."""
    surname, _, given = zone.partition("<<")
    return " ".join(surname.replace("<", " ").split()), " ".join(given.replace("<", " ").split())


def parse(text: str | None) -> Mrz | None:
    """The MRZ of a reading's value, or ``None`` when its length fits no known layout."""
    if not text:
        return None
    flat = clean(text)
    if not re.fullmatch(r"[A-Z0-9<]+", flat):
        return None
    if len(flat) == 72 and flat.startswith("IDFRA") and re.fullmatch(r"[0-9<]{6}", digits_only(flat[30:36])):
        return _cni_1995(flat[:36], flat[36:])
    if len(flat) not in LENGTHS:
        return None
    layout, width = LENGTHS[len(flat)]
    lines = [flat[start:start + width] for start in range(0, len(flat), width)]
    return {"TD1": _td1, "TD2": _td2, "TD3": _td3}[layout](lines)


def _dates(line: str, birth_at: int, expiry_at: int | None) -> tuple[str, str, list[Checked]]:
    birth = digits_only(line[birth_at:birth_at + 6])
    checks = [Checked("date de naissance", birth, digits_only(line[birth_at + 6]))]
    expiry = ""
    if expiry_at is not None:
        expiry = digits_only(line[expiry_at:expiry_at + 6])
        checks.append(Checked("date d'expiration", expiry, digits_only(line[expiry_at + 6])))
    return birth, expiry, checks


def _td1(lines: list[str]) -> Mrz:
    one, two, three = lines
    birth, expiry, dated = _dates(two, 0, 8)
    surname, given = names(three)
    checks = [Checked("numéro du document", one[5:14], digits_only(one[14])), *dated]
    if one[14] != "<":  # a longer number goes on in the optional data: its digits are not checked here
        checks.append(Checked("contrôle global", one[5:30] + two[0:7] + two[8:15] + two[18:29], digits_only(two[29])))
    return Mrz("TD1", lines, one[5:14].replace("<", ""), birth, expiry, surname, given, checks)


def _td2(lines: list[str]) -> Mrz:
    one, two = lines
    birth, expiry, dated = _dates(two, 13, 21)
    surname, given = names(one[5:36])
    checks = [Checked("numéro du document", two[0:9], digits_only(two[9])), *dated]
    if two[9] != "<":
        checks.append(Checked("contrôle global", two[0:10] + two[13:20] + two[21:35], digits_only(two[35])))
    return Mrz("TD2", lines, two[0:9].replace("<", ""), birth, expiry, surname, given, checks)


def _td3(lines: list[str]) -> Mrz:
    one, two = lines
    birth, expiry, dated = _dates(two, 13, 21)
    surname, given = names(one[5:44])
    checks = [Checked("numéro du document", two[0:9], digits_only(two[9])), *dated,
              Checked("numéro personnel", two[28:42], digits_only(two[42]))]
    if two[9] != "<":
        checks.append(Checked("contrôle global", two[0:10] + two[13:20] + two[21:43], digits_only(two[43])))
    return Mrz("TD3", lines, two[0:9].replace("<", ""), birth, expiry, surname, given, checks)


def _cni_1995(one: str, two: str) -> Mrz:
    birth, _, dated = _dates(two, 27, None)
    surname = " ".join(one[5:30].replace("<", " ").split())
    given = " ".join(two[13:27].replace("<", " ").split())
    number = two[0:12]
    checks = [Checked("numéro de la carte", number, digits_only(two[12])), *dated,
              Checked("contrôle global", one + two[0:35], digits_only(two[35]))]
    return Mrz("CNI 1995", [one, two], number.replace("<", ""), birth, "", surname, given, checks)
