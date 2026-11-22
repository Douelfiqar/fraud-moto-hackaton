"""licence_line_matches_print: the coded line on the back of a French licence card names its holder.

The card licence (model of 2013) carries one machine-readable line of 30 characters at
the bottom of its back: "D1", "FRA", the number of the card, the end of validity as
YYMMDD, then the first letters of the holder's surname ("<" as filler) and a last check
digit. Typing another surname on the front does not change that line.

The surname is compared as letters only: the letters found in the line's last eight
places before the check digit must be the beginning of the printed surname. The rule
needs the line and the printed surname in the same reading, that is a scan showing both
sides of the card; otherwise it does not apply. A line read with the wrong number of
characters is not judged: one character dropped by the reader shifts every place.
The reader's usual confusions between a letter and a digit are undone first.

Status: written from the published layout of the line and tested on made-up lines only.
It stays untrusted until it has been seen on real readings of cards scanned on both sides.
"""

from __future__ import annotations

import re

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, bare, value

PREFIX = "D1FRA"
LINE_LENGTH = 30
#: The surname's letters sit in these places of the line (counted from 0), just before the last check digit.
NAME_FROM, NAME_TO = 21, 29
#: Fewer letters than this say too little about a surname.
MIN_LETTERS = 3
_AS_LETTER = str.maketrans("0158", "OISB")


def surname_letters(line: str) -> str | None:
    """The surname's letters in a French licence line; ``None`` when the text is not such a line of 30 characters."""
    flat = re.sub(r"\s", "", line.upper())
    if not flat.startswith(PREFIX) or len(flat) != LINE_LENGTH:
        return None
    return re.sub(r"[^A-Z]", "", flat[NAME_FROM:NAME_TO].translate(_AS_LETTER))


@register
class LicenceLineMatchesPrint(ReadingCheck):
    name = "licence_line_matches_print"
    source = "general"
    description = "French licence card: the surname in the coded line of the back is the surname printed on the front."
    families = frozenset({"licence"})

    def applies(self, context: DocumentContext) -> bool:
        line = value(context, "mrz")[0] or ""
        return (super().applies(context) and re.sub(r"\s", "", line.upper()).startswith(PREFIX)
                and value(context, "nom")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        fields = ["nom", "mrz"]
        coded = surname_letters(value(context, "mrz")[0] or "")
        if coded is None:
            return self.result("cannot_tell", f"La ligne codée du permis n'a pas été lue avec ses {LINE_LENGTH} caractères : "
                               "ses places ne peuvent pas être jugées.", fields=fields)
        printed = re.sub(r"[^A-Z]", "", bare(value(context, "nom")[0] or "").translate(_AS_LETTER))
        if len(coded) < MIN_LETTERS or len(printed) < MIN_LETTERS:
            return self.result("cannot_tell", "Trop peu de lettres du nom dans la ligne codée ou sur le recto pour les "
                               "comparer.", fields=fields)
        if printed.startswith(coded) or coded.startswith(printed):
            return self.result("pass", "Le nom de la ligne codée du verso est celui imprimé au recto.", fields=fields)
        return self.result(
            "fail",
            "Le nom porté par la ligne codée au verso du permis n'est pas le nom imprimé au recto : la ligne codée ne "
            "change pas quand un nom est retapé sur la carte.",
            fields=fields,
            clues=["nom imprimé différent de celui de la ligne codée du permis"],
            fraud_type="identité modifiée",
        )
