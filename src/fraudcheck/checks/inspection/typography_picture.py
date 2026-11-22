"""typography_picture: no zone whose lettering clearly differs from its neighbours, as seen on the pictures.

The picture-side twin of the text-PDF typography checks: it covers photos and scans,
where there is no text layer. A field typed again over a genuine document is drawn by a
computer on top of a photographed or printed page: it is in another font, sharper than
its neighbours, or sits on a patch of background.

What counts was measured on documents whose status is known:

- only differences the model calls "clear" count; "slight" ones never do, since the
  camera alone can cause them;
- a clear difference counts when it is in the font, the sharpness or a background patch.
  Weight, size, colour, spacing or alignment alone are also found on genuine forms (a
  bank prints its BIC in bold, a column is too narrow for its value) and are only reported;
- a PDF with real text is not judged here: its own fonts are checked directly
  (``font``, ``covered_text``, ``baseline_shift``);
- a scan saved in layers by an office scanner (background picture plus one-bit text
  masks) is not judged either: its characters differ in sharpness and weight from one
  layer to the other without any retouching.
"""

from __future__ import annotations

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.inspection._inspection import ASPECTS, InspectionCheck, known_names, match, place, real_page

#: Aspects that point to text drawn by a computer over the page, not to a feature of the form.
STRONG_ASPECTS = frozenset({"font", "sharpness", "background_patch"})


@register
class TypographyPicture(InspectionCheck):
    name = "typography_picture"
    source = "general"
    description = ("Inspection: no zone whose lettering clearly differs from its neighbours in font or sharpness, "
                   "or sits on a background patch.")

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and context.record.kind != "pdf_text"

    def run(self, context: DocumentContext) -> CheckResult:
        if blocked := self.not_usable(context):
            return blocked
        answer, known = context.inspection.answer, known_names(context)
        clear = [item for item in answer.typography if item.strength == "clear" and item.aspects]
        strong = [item for item in clear if STRONG_ASPECTS & set(item.aspects)]
        minor = [item for item in clear if item not in strong]
        slight = [item for item in answer.typography if item.strength == "slight" or not item.aspects]
        details = {"observations": [item.model_dump() for item in answer.typography], "capture": answer.capture,
                   "fine_print": [item.model_dump() for item in answer.fine_print]}  # information only, for now
        if any(page.scanner_layers for page in context.record.pages):
            return self.result("cannot_tell", "Scan enregistré en couches par le scanner (fond et masques de texte) : "
                               "netteté et graisse y varient d'une couche à l'autre sans retouche, le lettrage n'est "
                               "pas jugé sur l'image.", details=details)
        if strong:
            named = [match(item.zone, known) for item in strong]
            parts = [place(real_page(context, item.page), name) + " : "
                     + ", ".join(ASPECTS[aspect] for aspect in dict.fromkeys(item.aspects))
                     for item, name in zip(strong, named, strict=True)]
            return self.result(
                "fail",
                "Lettrage nettement différent de celui des zones voisines, vu sur l'image : " + " ; ".join(parts) + ".",
                fields=sorted({name for name in named if name}),
                clues=["lettrage différent sur une zone de l'image"],
                fraud_type="zone modifiée",
                details=details,
            )
        evidence = "Aucune zone dont la police ou la netteté diffère nettement de ses voisines."
        if minor:
            evidence += (f" {len(minor)} différence(s) de graisse, de taille, de couleur ou d'espacement, que des "
                         "formulaires authentiques présentent aussi.")
        if slight:
            evidence += f" {len(slight)} différence(s) légère(s), qui peuvent venir de la prise de vue."
        return self.result("pass", evidence, details=details)
