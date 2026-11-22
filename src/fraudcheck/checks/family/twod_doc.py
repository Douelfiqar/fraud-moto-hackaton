"""twod_doc_matches_print: the document's own 2D-Doc names the person printed on it.

A 2D-Doc is the DataMatrix code French issuers print on a document (identity card,
prefecture attestation, tax notice, proof of address). It repeats the document's key
data and carries the issuer's signature. Typing another name over the document does not
change the code: when neither the printed surname nor any printed first name is in the
code's data, the print and the code describe two different people.

Ingest reads the code on the laptop (``Page.barcodes``); this check compares it with the
reading of the same document, and with nothing else.

Limits, on purpose:
- one match is enough to pass (the surname, or one first name): a married or usage name
  can differ from the name the issuer signed;
- the signature is not verified, which would need the issuers' certificates. A forger
  who rebuilt the whole code is not caught here;
- a document without a readable 2D-Doc is not judged, and neither is any document when
  the local reader is not installed.

twod_doc_date_matches_print: the header of the code holds the day the issuer signed it.
A document is signed when it is issued: the signing day and the printed date of issue
are a few days apart at most. A code signed long before the date printed on the document
means that date was typed again, as when an old attestation is made to look valid.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, normalise, parse_date, value

#: Every 2D-Doc starts with "DC" and two digits for the version of the format.
_HEADER = re.compile(r"DC\d{2}")
#: Characters of the header before the data in every version (marker, version, issuer, certificate, two dates, type).
HEADER_CHARS = 22
#: The signature follows the unit separator; it is random-looking letters, never searched for a name.
_BEFORE_SIGNATURE = re.compile(r"\x1f|<US>")
#: A name part shorter than this (LE, DE, EL) is found in almost any text: not compared.
MIN_NAME_LETTERS = 3
NAME_FIELDS = ("nom", "prenoms", "titulaire")


def code_data(text: str) -> str | None:
    """The data of a 2D-Doc in capitals without accents; ``None`` when the text is not a 2D-Doc."""
    if not _HEADER.match(text) or len(text) <= HEADER_CHARS:
        return None
    return normalise(_BEFORE_SIGNATURE.split(text[HEADER_CHARS:], maxsplit=1)[0]).upper()


def name_parts(text: str | None) -> list[str]:
    """The parts of a printed name worth looking for: capitals without accents, three letters or more."""
    return [part for part in re.findall(r"[A-Z]+", normalise(text or "").upper()) if len(part) >= MIN_NAME_LETTERS]


def documents_codes(context: DocumentContext) -> list[str]:
    """The data of every 2D-Doc read on the document's own pages."""
    found = (code_data(code.text) for page in context.record.pages for code in page.barcodes or [])
    return [data for data in found if data]


#: The header writes its dates as a number of days since 1 January 2000, in four hexadecimal digits;
#: the day of signature sits in these places, and "FFFF" means no date.
_EPOCH = date(2000, 1, 1)
SIGNED_FROM, SIGNED_TO = 16, 20


def signing_date(text: str) -> date | None:
    """The day the issuer signed a 2D-Doc, from its header; ``None`` when the text is not a 2D-Doc or gives no date."""
    digits = text[SIGNED_FROM:SIGNED_TO].upper()
    if not _HEADER.match(text) or not re.fullmatch(r"[0-9A-F]{4}", digits) or digits == "FFFF":
        return None
    return _EPOCH + timedelta(days=int(digits, 16))


def signing_dates(context: DocumentContext) -> list[date]:
    """The signing day of every 2D-Doc read on the document's own pages."""
    found = (signing_date(code.text) for page in context.record.pages for code in page.barcodes or [])
    return [day for day in found if day]


@register
class TwoDDocMatchesPrint(ReadingCheck):
    name = "twod_doc_matches_print"
    source = "general"
    description = "2D-Doc: the document's signed code holds the printed surname or a printed first name."

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and bool(documents_codes(context))

    def run(self, context: DocumentContext) -> CheckResult:
        codes = documents_codes(context)
        printed = {name: name_parts(value(context, name)[0]) for name in NAME_FIELDS}
        compared = [name for name, parts in printed.items() if parts]
        if not compared:
            return self.result("cannot_tell", "Le document porte un code 2D-Doc, mais ni le nom ni les prénoms imprimés "
                               "n'ont pu être lus pour les comparer à ses données.", fields=list(NAME_FIELDS[:2]))
        fields = [*compared, "code 2D-Doc"]
        matching = [name for name in compared if any(part in data for part in printed[name] for data in codes)]
        if matching:
            return self.result("pass", "Les données du code 2D-Doc porté par le document contiennent le nom ou un prénom "
                               "imprimé.", fields=fields, details={"matching": matching, "codes": len(codes)})
        return self.result(
            "fail",
            "Le code 2D-Doc porté par le document, qui reprend ses données signées par l'émetteur, ne contient ni le nom "
            "ni aucun prénom imprimé : le texte imprimé et le code ne décrivent pas la même personne.",
            fields=fields,
            clues=["nom imprimé absent des données du code 2D-Doc"],
            fraud_type="identité modifiée",
            details={"compared": compared, "codes": len(codes)},
        )


@register
class TwoDDocDateMatchesPrint(ReadingCheck):
    name = "twod_doc_date_matches_print"
    source = "general"
    description = "2D-Doc: the issuer signed the code around the date of issue printed on the document."

    def applies(self, context: DocumentContext) -> bool:
        return (super().applies(context) and bool(signing_dates(context))
                and value(context, "date_delivrance")[1] == "read")

    def run(self, context: DocumentContext) -> CheckResult:
        fields = ["date_delivrance", "code 2D-Doc"]
        issued = parse_date(value(context, "date_delivrance")[0], context.settings.today().year).value
        if issued is None:
            return self.result("cannot_tell", "Date de délivrance imprimée illisible : elle ne peut pas être comparée à la "
                               "date de signature du code 2D-Doc.", fields=fields)
        limit = context.settings.twod_doc_max_days
        gaps = [(signed - issued).days for signed in signing_dates(context)]
        nearest = min(gaps, key=abs)
        if abs(nearest) <= limit:
            return self.result("pass", "Le code 2D-Doc a été signé à la date de délivrance imprimée, à quelques jours près.",
                               fields=fields, details={"days_from_issue": nearest})
        return self.result(
            "fail",
            f"Le code 2D-Doc porté par le document a été signé par l'émetteur à plus de {limit} jours de la date de "
            "délivrance imprimée : la date imprimée n'est pas celle du document que l'émetteur a signé.",
            fields=fields,
            clues=["date de délivrance imprimée différente de la date de signature du code 2D-Doc"],
            fraud_type="date modifiée",
            details={"days_from_issue": nearest},
        )
