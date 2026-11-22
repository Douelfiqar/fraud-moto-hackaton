"""RIB: IBAN check digits (mod 97), RIB key, and the IBAN against its printed parts.

Before a failure counts, the usual single-character misreads are tried (O and 0,
I and 1, B and 8, S and 5): if one of them makes the check pass, it passes with a note.
"""

from __future__ import annotations

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, compact, misread_note, passes_after_misread, value

#: IBAN lengths of the countries most likely to appear (ISO 13616 registry).
IBAN_LENGTHS = {"FR": 27, "MC": 27, "DE": 22, "BE": 16, "ES": 24, "IT": 27, "PT": 25, "NL": 18, "LU": 20,
                "IE": 22, "AT": 20, "GB": 22, "CH": 21, "PL": 28, "RO": 24}
_RIB_LETTERS = {**dict.fromkeys("AJ", "1"), **dict.fromkeys("BKS", "2"), **dict.fromkeys("CLT", "3"),
                **dict.fromkeys("DMU", "4"), **dict.fromkeys("ENV", "5"), **dict.fromkeys("FOW", "6"),
                **dict.fromkeys("GPX", "7"), **dict.fromkeys("HQY", "8"), **dict.fromkeys("IRZ", "9")}


def iban_valid(iban: str) -> bool:
    """ISO 13616: right length for the country, and the number mod 97 equals 1."""
    if len(iban) < 15 or not iban[:2].isalpha() or not iban[2:4].isdigit() or not iban.isalnum():
        return False
    if IBAN_LENGTHS.get(iban[:2], len(iban)) != len(iban):
        return False
    digits = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def rib_key_valid(bank: str, branch: str, account: str, key: str) -> bool:
    """French RIB key: 97 - ((89 x bank + 15 x branch + 3 x account) mod 97), letters of the account as digits."""
    if not (len(bank) == 5 and len(branch) == 5 and len(account) == 11 and len(key) == 2):
        return False
    account_digits = "".join(_RIB_LETTERS.get(ch, ch) for ch in account)
    if not (bank.isdigit() and branch.isdigit() and account_digits.isdigit() and key.isdigit()):
        return False
    return 97 - (89 * int(bank) + 15 * int(branch) + 3 * int(account_digits)) % 97 == int(key)


@register
class IbanCheckDigits(ReadingCheck):
    name = "iban_check_digits"
    source = "general"
    description = "RIB: the IBAN's check digits are right (mod 97), after trying the usual misreads."
    families = frozenset({"rib"})

    def run(self, context: DocumentContext) -> CheckResult:
        text, state = value(context, "iban")
        if state != "read":
            return self.result("cannot_tell", "IBAN absent ou illisible.", fields=["iban"])
        iban = compact(text)
        if iban_valid(iban):
            return self.result("pass", "Les chiffres de contrôle de l'IBAN sont justes.", fields=["iban"])
        if found := passes_after_misread(iban, iban_valid):
            return self.result("pass", "Les chiffres de contrôle de l'IBAN sont justes" + misread_note(found) + ".",
                               fields=["iban"], details={"misread": True})
        return self.result("fail", "Les chiffres de contrôle de l'IBAN sont faux, même en tenant compte des confusions "
                           "de lecture courantes.", fields=["iban"], clues=["IBAN invalide (clé de contrôle)"])


def _parts(context: DocumentContext) -> tuple[dict[str, str], str] | None:
    """The four printed parts of a French RIB, or the same parts taken from a French IBAN."""
    names = ("code_banque", "code_guichet", "numero_compte", "cle_rib")
    printed = {name: value(context, name) for name in names}
    if all(state == "read" for _, state in printed.values()):
        return {name: compact(text) for name, (text, _) in printed.items()}, "parties imprimées"
    iban_text, state = value(context, "iban")
    iban = compact(iban_text) if state == "read" else ""
    if iban.startswith("FR") and len(iban) == 27:
        return dict(zip(names, (iban[4:9], iban[9:14], iban[14:25], iban[25:27]), strict=True)), "IBAN"
    return None


@register
class RibKey(ReadingCheck):
    name = "rib_key"
    source = "general"
    description = "RIB: the RIB key matches the bank code, branch code and account number."
    families = frozenset({"rib"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and _parts(context) is not None

    def run(self, context: DocumentContext) -> CheckResult:
        parts, origin = _parts(context)
        fields = list(parts) if origin == "parties imprimées" else ["iban"]
        joined = "".join(parts.values())

        def test(candidate: str) -> bool:
            return rib_key_valid(candidate[:5], candidate[5:10], candidate[10:21], candidate[21:23])

        if test(joined):
            return self.result("pass", f"La clé RIB est juste ({origin}).", fields=fields)
        if found := passes_after_misread(joined, test):
            return self.result("pass", f"La clé RIB est juste ({origin}){misread_note(found)}.", fields=fields,
                               details={"misread": True})
        return self.result("fail", f"La clé RIB ne correspond pas aux codes banque, guichet et compte ({origin}).",
                           fields=fields, clues=["clé RIB fausse"])


@register
class IbanMatchesParts(ReadingCheck):
    name = "iban_parts"
    source = "general"
    description = "RIB: a French IBAN contains the bank code, branch code, account number and key printed apart."
    families = frozenset({"rib"})

    def applies(self, context: DocumentContext) -> bool:
        if not super().applies(context):
            return False
        iban_text, state = value(context, "iban")
        printed = any(value(context, name)[1] == "read" for name in ("code_banque", "code_guichet", "numero_compte", "cle_rib"))
        return state == "read" and compact(iban_text).startswith("FR") and printed

    def run(self, context: DocumentContext) -> CheckResult:
        iban = compact(value(context, "iban")[0])
        if len(iban) != 27:
            return self.result("cannot_tell", "L'IBAN français lu n'a pas 27 caractères : ses parties ne peuvent pas "
                               "être comparées.", fields=["iban"])
        expected = {"code_banque": iban[4:9], "code_guichet": iban[9:14], "numero_compte": iban[14:25], "cle_rib": iban[25:27]}
        compared, differing, repaired = [], [], False
        for name, inside in expected.items():
            text, state = value(context, name)
            if state != "read":
                continue
            compared.append(name)
            printed = compact(text)
            if printed == inside:
                continue
            if passes_after_misread(printed, lambda candidate, inside=inside: candidate == inside):
                repaired = True
                continue
            differing.append(name)
        fields = ["iban", *compared]
        if differing:
            return self.result("fail", "L'IBAN ne contient pas les parties imprimées à côté : " + ", ".join(differing) + ".",
                               fields=fields + differing, clues=["IBAN et RIB détaillé ne concordent pas"])
        note = " (après correction d'une confusion de lecture courante)" if repaired else ""
        return self.result("pass", f"L'IBAN contient bien les parties imprimées à côté{note}.", fields=fields)
