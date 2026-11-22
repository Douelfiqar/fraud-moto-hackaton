"""Carte grise: the vehicle category (J.1) against engine size (P.1) and power (P.2).

Limits: CL (moped) up to 50 cm3; MTL (light motorcycle) up to 125 cm3 and 11 kW;
MTT1 up to 25 kW (APRIL's carte grise fiche: "moto bridée en MTT1 -> P.2 maxi 25 kW").
"""

from __future__ import annotations

import re

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, parse_number, value

#: Category: (largest engine size in cm3, largest power in kW); None means no limit to check.
LIMITS = {"CL": (50.0, None), "MTL": (125.0, 11.0), "MTT1": (None, 25.0)}


def category(text: str | None) -> str | None:
    """The J.1 code as a key of ``LIMITS`` ('MTT 1' and 'mtt1' give 'MTT1'), or ``None``."""
    if not text:
        return None
    code = re.sub(r"[^A-Z0-9]", "", text.upper())
    return code if code in LIMITS else None


@register
class CategoryAgainstPower(ReadingCheck):
    name = "cg_category_power"
    source = "fiche"
    description = "Carte grise: category CL, MTL or MTT1 against engine size and power."
    families = frozenset({"carte_grise"})

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and category(value(context, "genre")[0]) is not None

    def run(self, context: DocumentContext) -> CheckResult:
        code = category(value(context, "genre")[0])
        max_cm3, max_kw = LIMITS[code]
        cm3_text, cm3_state = value(context, "cylindree")
        kw_text, kw_state = value(context, "puissance_kw")
        cm3 = parse_number(cm3_text) if cm3_state == "read" else None
        kw = parse_number(kw_text) if kw_state == "read" else None
        over = []
        if max_cm3 is not None and cm3 is not None and cm3 > max_cm3:
            over.append(("cylindrée", f"{max_cm3:g} cm3"))
        if max_kw is not None and kw is not None and kw > max_kw:
            over.append(("puissance", f"{max_kw:g} kW"))
        fields = ["genre", "cylindree", "puissance_kw"]
        details = {"category": code, "cm3": cm3, "kw": kw}
        if over:
            what = " et ".join(f"la {quantity} dépasse {limit}" for quantity, limit in over)
            return self.result("fail", f"Le genre du véhicule (rubrique J.1) ne permet pas ce véhicule : {what}.",
                               fields=fields, clues=[f"genre incompatible avec la {over[0][0]}"], details=details)
        missing = (max_cm3 is not None and cm3 is None) or (max_kw is not None and kw is None)
        if missing:
            return self.result("cannot_tell", "La cylindrée ou la puissance est illisible : le genre du véhicule ne peut "
                               "pas être vérifié.", fields=fields, details=details)
        return self.result("pass", "Cylindrée et puissance compatibles avec le genre du véhicule (rubrique J.1).",
                           fields=fields, details=details)
