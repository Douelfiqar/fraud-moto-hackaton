"""RIB: the BIC's format, and the cases APRIL's RIB fiche refuses.

bic_format: a BIC has 8 or 11 characters: four letters for the bank, two for the
country, two letters or digits for the place, and optionally three for the branch
(ISO 9362). Digits read where letters must be are taken as the usual misreads.
rib_refused: the fiche accepts RIBs of the European Union only, and refuses Monaco
RIBs, savings accounts (Livret A) and "My Pocket" accounts. A refused RIB is not a
fake: this check informs the person who validates, like an expired document.
"""

from __future__ import annotations

import re

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, compact, normalise, value

BIC = re.compile(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?")
_LETTER_FOR = str.maketrans("0158", "OISB")
#: Member states of the European Union, by the two letters that open their IBANs.
EU = frozenset("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split())


@register
class BicFormat(ReadingCheck):
    name = "bic_format"
    source = "general"
    description = "RIB: the BIC has 8 or 11 characters: bank (4 letters), country (2), place (2), branch (3, optional)."
    families = frozenset({"rib"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and value(context, "bic")[1] == "read"

    def run(self, context: DocumentContext) -> CheckResult:
        bic = re.sub(r"[^A-Z0-9]", "", compact(value(context, "bic")[0]))
        repaired = bic[:6].translate(_LETTER_FOR) + bic[6:]
        if BIC.fullmatch(bic) or BIC.fullmatch(repaired):
            note = "" if BIC.fullmatch(bic) else " (un chiffre lu parmi les six premières lettres, confusion courante)"
            return self.result("pass", f"BIC au format attendu, {len(bic)} caractères{note}.", fields=["bic"])
        return self.result("fail", f"BIC au format impossible ({len(bic)} caractères ; attendu : 8 ou 11, dont six "
                           "lettres en tête).", fields=["bic"], clues=["BIC au format impossible"],
                           fraud_type="valeur modifiée", details={"length": len(bic)})


@register
class RibRefused(ReadingCheck):
    name = "rib_refused"
    source = "fiche"
    description = "RIB: not a case the fiche refuses (outside the EU, Monaco, savings account, My Pocket)."
    families = frozenset({"rib"})

    def run(self, context: DocumentContext) -> CheckResult:
        iban, iban_state = value(context, "iban")
        kind, kind_state = value(context, "type_compte")
        bank, _ = value(context, "banque")
        country = compact(iban)[:2] if iban_state == "read" else ""
        words = normalise(" ".join(filter(None, (kind, bank))))
        refused = []
        if country == "MC":
            refused.append("RIB monégasque")
        elif country and country.isalpha() and country not in EU:
            refused.append("RIB hors de l'Union européenne")
        if re.search(r"\blivret\b|\bepargne\b", normalise(kind or "")):
            refused.append("compte épargne")
        if "mypocket" in words.replace(" ", ""):
            refused.append("compte « My Pocket »")
        fields = ["iban", "type_compte"]
        if refused:
            return self.result("fail", "Cas refusé par la fiche RIB : " + ", ".join(refused) + ".", fields=fields,
                               clues=[f"refusé par la fiche : {what}" for what in refused])
        if not country and kind_state != "read":
            return self.result("cannot_tell", "IBAN et type de compte illisibles : les cas refusés par la fiche ne "
                               "peuvent pas être vérifiés.", fields=fields)
        return self.result("pass", "Aucun cas refusé par la fiche (RIB de l'Union européenne, ni épargne, ni My Pocket).",
                           fields=fields)
