"""scan_overlay: nothing typed or drawn on top of a scanned page.

A scanner writes a picture of the sheet and nothing else. When a PDF that is a scan
also carries a few characters of visible text, filled shapes or notes, they were laid on
the picture afterwards: a year typed over a white patch, a bar drawn over a line, ink
strokes over a value. Ingest records these facts for each scanned page, that is a page
holding a picture in a PDF without real text (``visible_chars``, ``overlay_shapes``,
``overlay_notes``); this check only reads them.

Two things a scanner does write are not counted: the plain white page background some
draw under the picture, and the invisible text layer some add to make the picture
searchable.
"""

from __future__ import annotations

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, register


def _pages(numbers: list[int], many: bool) -> str:
    return f" (page{'s' if len(numbers) > 1 else ''} {', '.join(str(n) for n in numbers)})" if many else ""


@register
class ScanOverlay(Check):
    name = "scan_overlay"
    source = "general"
    description = "Scanned PDF: no visible text, shape or note laid on top of the scan."

    def applies(self, context: DocumentContext) -> bool:
        return context.record.kind == "pdf_scan" and bool(context.record.pages)

    def run(self, context: DocumentContext) -> CheckResult:
        scanned = [page for page in context.record.pages if page.overlay_shapes is not None]
        fields = ["contenu de la page"]
        if not scanned:
            return self.result("cannot_tell", "Aucune page scannée relevée dans ce PDF : ses pages ne portent pas "
                               "d'image, ou les ajouts n'ont pas été relevés à l'ingestion (relancer « ingest --force »).",
                               fields=fields)
        typed = [(page.number, page.visible_chars) for page in scanned if page.visible_chars]
        shapes = [(page.number, page.overlay_shapes) for page in scanned if page.overlay_shapes]
        notes = [(page.number, page.overlay_notes) for page in scanned if page.overlay_notes]
        if not (typed or shapes or notes):
            return self.result("pass", "Rien n'est posé par-dessus la page scannée : ni texte visible, ni forme, ni "
                               "annotation.", fields=fields)
        many = len(context.record.pages) > 1
        parts, clues = [], []
        if typed:
            count = sum(chars for _, chars in typed)
            parts.append(f"{count} caractère(s) de texte tapé{_pages([n for n, _ in typed], many)}")
            clues.append("texte tapé par-dessus un document scanné")
        if shapes:
            count = sum(number for _, number in shapes)
            parts.append(f"{count} forme(s) pleine(s) dessinée(s){_pages([n for n, _ in shapes], many)}")
            clues.append("formes dessinées par-dessus un document scanné")
        if notes:
            kinds = sorted({kind for _, kinds in notes for kind in kinds})
            parts.append(f"annotation(s) de type {', '.join(kinds)}{_pages([n for n, _ in notes], many)}")
            clues.append("annotations ajoutées sur un document scanné")
        return self.result(
            "fail",
            "Des éléments ont été ajoutés par-dessus la page scannée : " + " ; ".join(parts) + ". Un scanner n'écrit "
            "qu'une image : ce texte, ces formes ou ces annotations ont été posés après la numérisation.",
            fields=fields,
            clues=clues,
            fraud_type="zone modifiée (ajout sur un scan)",
            details={"typed": typed, "shapes": shapes, "notes": notes},
        )
