"""base_form: every mandatory zone of the base form (photo, signature, machine-readable lines...) is there, in place.

A zone the model reports "absent" (it belongs on the face shown and is not there) fails.
"displaced" fails only when the type's layout is fixed by regulation (``layout =
"fixed"``); when it varies with the issuer or the version (a RIB, a relevé of the old
format), only presence is judged. A zone that belongs to the other face, or to another
version of the form, does not count; neither does a zone the picture does not let one see.
"""

from __future__ import annotations

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.inspection._inspection import InspectionCheck, base_form, match, real_page

#: When a zone is reported more than once, the report that counts: the lowest rank.
_RANK = {"present": 0, "displaced": 1, "not_applicable": 2, "unreadable": 3, "absent": 4}


@register
class BaseFormZones(InspectionCheck):
    name = "base_form"
    source = "general"
    description = "Inspection: every mandatory zone of the base form is there, in its place (config/doc_types, [base_form])."

    def applies(self, context: DocumentContext) -> bool:
        form = base_form(context) if super().applies(context) else None
        return bool(form and form.zones)

    def run(self, context: DocumentContext) -> CheckResult:
        if blocked := self.not_usable(context):
            return blocked
        form, answer = base_form(context), context.inspection.answer
        names = [zone.name for zone in form.zones]
        placed = {zone.name for zone in form.zones if zone.where.strip()}  # zones whose place the form states
        seen = {}
        for observation in answer.zones:
            # A zone can be reported once per page: the most favourable report counts (present on page 1
            # outweighs absent on page 2).
            name = match(observation.name, names)
            if name and (name not in seen or _RANK[observation.state] < _RANK[seen[name].state]):
                seen[name] = observation
        cut = answer.readable == "partly"  # part of the document is out of the picture: a zone may be there, unseen

        def fails(name: str) -> bool:
            state = seen[name].state
            if state == "absent":
                return not cut
            return state == "displaced" and form.layout == "fixed" and name in placed

        wrong = [(name, seen[name]) for name in names if name in seen and fails(name)]
        present = [name for name in names if name in seen and seen[name].state in ("present", "displaced")]
        elsewhere = [name for name in names if name in seen and seen[name].state == "not_applicable"]
        unseen = [name for name in names if name not in seen or seen[name].state == "unreadable"
                  or (seen[name].state == "absent" and cut)]
        details = {
            "zones": {name: {"state": seen[name].state, "page": seen[name].page} for name in names if name in seen},
            "not_reported": [name for name in names if name not in seen],
            "layout": form.layout,
            "capture": answer.capture,
        }
        if wrong:
            parts = [f"« {name} » {'absente' if observation.state == 'absent' else 'pas à sa place'}"
                     + (f" (page {page})" if (page := real_page(context, observation.page)) else "")
                     for name, observation in wrong]
            sources = {zone.name: zone.source for zone in form.zones}
            return self.result(
                "fail",
                "Formulaire différent du modèle : " + " ; ".join(parts) + ".",
                fields=[name for name, _ in wrong],
                clues=["zone obligatoire absente ou déplacée"],
                fraud_type="formulaire non conforme",
                details=details,
                source="fiche" if any(sources[name].startswith("Fiche") for name, _ in wrong) else "general",
            )
        if not present:
            return self.result("cannot_tell", "Aucune zone obligatoire du modèle n'a pu être vue sur les images.",
                               fields=names, details=details)
        evidence = (f"{len(present)} zone(s) obligatoire(s) du modèle vue(s) à leur place" if form.layout == "fixed"
                    else f"{len(present)} zone(s) obligatoire(s) du modèle présente(s) (mise en page propre à l'émetteur "
                    "ou à la version : seule la présence est jugée)")
        if elsewhere:
            evidence += f", {len(elsewhere)} propre(s) à l'autre face ou à une autre version du formulaire"
        if unseen:
            evidence += f", {len(unseen)} non vue(s)"
        return self.result("pass", evidence + ".", fields=present, details=details)
