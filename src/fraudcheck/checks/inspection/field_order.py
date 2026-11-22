"""field_order: the fields come in the order of the base form.

Some forms print their fields in a fixed order: the European licence (1, 2, 3, 4a, 4b,
4c, 5, 9), the French RIB (code banque, code guichet, numéro de compte, clé), the
relevé since 24 July 2025 (sections A to E). A fake built on a wrong template, or
edited carelessly, can swap two of them. Fails only when the swapped fields the model
names are fields of the base form.
"""

from __future__ import annotations

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.inspection._inspection import InspectionCheck, base_form, match


@register
class FieldOrder(InspectionCheck):
    name = "field_order"
    source = "general"
    description = "Inspection: the fields come in the order of the base form (licence 1 to 9, RIB codes, relevé A to E)."

    def applies(self, context: DocumentContext) -> bool:
        form = base_form(context) if super().applies(context) else None
        return bool(form and form.field_order)

    def run(self, context: DocumentContext) -> CheckResult:
        if blocked := self.not_usable(context):
            return blocked
        form, order = base_form(context), context.inspection.answer.field_order
        names = list(form.field_order)
        swapped = sorted({name for item in order.swapped if (name := match(item, names))}, key=names.index)
        label_sources = {label.text: label.source for label in form.labels}
        source = "fiche" if label_sources.get(names[0], "").startswith("Fiche") else "general"
        details = {"verdict": order.verdict, "swapped": order.swapped, "capture": context.inspection.answer.capture}
        if order.verdict == "out_of_order" and swapped:
            return self.result(
                "fail",
                "Rubriques dans un ordre différent du modèle : " + ", ".join(f"« {name} »" for name in swapped) + ".",
                fields=swapped,
                clues=["rubriques inversées"],
                fraud_type="formulaire non conforme",
                details=details,
                source=source,
            )
        if order.verdict == "in_order":
            return self.result("pass", "Les rubriques suivent l'ordre du modèle (" + " ; ".join(names) + ").",
                               fields=names, details=details, source=source)
        why = " (rubriques citées inconnues du modèle)" if order.verdict == "out_of_order" else ""
        return self.result("cannot_tell", f"L'ordre des rubriques n'a pas pu être comparé au modèle{why}.",
                           fields=names, details=details, source=source)
