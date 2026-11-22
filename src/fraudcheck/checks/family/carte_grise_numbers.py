"""Carte grise: the registration number and the vehicle identification number (VIN).

cg_plate_format: the number has the current format (SIV since 15 April 2009: two
letters, three digits, two letters, never I, O or U) or the former one (FNI: one to
four digits, one to three letters, the department). A number in the current format
cannot belong to a registration dated before 15 April 2009 (rubrique I). Mopeds
registered between 2004 and 2009 had a format of their own: not judged.
cg_vin_format: a vehicle first registered from 2000 (setting) carries a VIN of 17
characters without I, O or Q (ISO 3779).
"""

from __future__ import annotations

import re
from datetime import date

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, misread_note, parse_date, passes_after_misread, value

SIV_FROM = date(2009, 4, 15)
SIV = re.compile(r"[A-HJ-NP-TV-Z]{2}\d{3}[A-HJ-NP-TV-Z]{2}")
FNI = re.compile(r"\d{1,4}[A-Z]{1,3}(?:\d{2}|2A|2B|97\d)")
VIN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")


def plate_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def plate_kind(plate: str) -> str | None:
    if SIV.fullmatch(plate):
        return "SIV"
    if FNI.fullmatch(plate):
        return "FNI"
    return None


@register
class PlateFormat(ReadingCheck):
    name = "cg_plate_format"
    source = "general"
    description = "Carte grise: the registration number has the current (AA-123-AA) or former (123 ABC 75) format."
    families = frozenset({"carte_grise"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "immatriculation")[1] == "read"

    def run(self, context: DocumentContext) -> CheckResult:
        plate = plate_key(value(context, "immatriculation")[0])
        fields = ["immatriculation"]
        kind, note = plate_kind(plate), ""
        if kind is None and (found := passes_after_misread(plate, lambda candidate: plate_kind(candidate) is not None)):
            position, _, meant = found
            kind, note = plate_kind(plate[:position] + meant + plate[position + 1:]), misread_note(found)
        if kind is None:
            if plate_key(value(context, "genre")[0] or "") == "CL":
                return self.result("cannot_tell", "Numéro d'un cyclomoteur hors des formats SIV et FNI : peut-être le "
                                   "format propre aux cyclomoteurs de 2004 à 2009.", fields=[*fields, "genre"])
            return self.result("fail", "Le numéro d'immatriculation n'a ni le format actuel (AA-123-AA, sans I, O ni U) "
                               "ni l'ancien format (123 ABC 75).", fields=fields,
                               clues=["numéro d'immatriculation au format impossible"], fraud_type="valeur modifiée")
        dated_text, dated_state = value(context, "date_certificat")
        dated = parse_date(dated_text, context.settings.today().year).value if dated_state == "read" else None
        if kind == "SIV" and dated is not None and dated < SIV_FROM:
            return self.result("fail", "Numéro au format actuel (SIV) sur une immatriculation datée d'avant le 15 avril "
                               "2009, date de mise en service de ce format.", fields=[*fields, "date_certificat"],
                               clues=["format SIV antérieur à 2009"], fraud_type="date modifiée")
        what = "au format actuel (SIV)" if kind == "SIV" else "à l'ancien format (FNI)"
        return self.result("pass", f"Numéro d'immatriculation {what}{note}.", fields=fields, details={"format": kind})


@register
class VinFormat(ReadingCheck):
    name = "cg_vin_format"
    source = "general"
    description = "Carte grise: the VIN has 17 characters without I, O or Q (vehicles first registered from 2000)."
    families = frozenset({"carte_grise"})

    def applies(self, context: DocumentContext) -> bool:
        if not super().applies(context) or value(context, "numero_identification")[1] != "read":
            return False
        first = parse_date(value(context, "date_premiere_immatriculation")[0], context.settings.today().year).value
        return first is not None and first >= context.settings.vin_rule_from

    def run(self, context: DocumentContext) -> CheckResult:
        vin = re.sub(r"[^A-Z0-9]", "", value(context, "numero_identification")[0].upper())
        fields = ["numero_identification", "date_premiere_immatriculation"]
        if VIN.fullmatch(vin):
            return self.result("pass", "Numéro d'identification du véhicule de 17 caractères, sans I, O ni Q.",
                               fields=fields)
        if len(vin) == 17 and VIN.fullmatch(vin.translate(str.maketrans("IOQ", "100"))):
            return self.result("pass", "Numéro d'identification de 17 caractères ; un I, O ou Q lu y est sans doute un "
                               "1 ou un 0 (confusion de lecture courante).", fields=fields)
        problem = (f"{len(vin)} caractères au lieu de 17" if len(vin) != 17
                   else "une lettre interdite (I, O ou Q)")
        return self.result("fail", f"Numéro d'identification du véhicule impossible : {problem}.", fields=fields,
                           clues=["numéro d'identification du véhicule au format impossible"],
                           fraud_type="valeur modifiée", details={"length": len(vin)})
