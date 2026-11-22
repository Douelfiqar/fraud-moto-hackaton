"""generated_size: a picture whose pixel count is the fixed budget of an image-generation tool, whatever its shape.

A camera gives a fixed shape per device; a scanner, a size tied to the paper and the
resolution. An image-generation tool that redraws a picture keeps its proportions but
spends a fixed number of pixels, whatever the shape (1024 x 1536 = 1,572,864 for the
tool known here); a camera or a scanner does not land on that count by chance.
The size measured is the picture's own
(the received file, or the single picture inside a PDF page), never a page Ingest drew.
A PDF page with several pictures or none, or a document with real text, gives no answer.
"""

from __future__ import annotations

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, percent, register


def _pixels(count: int) -> str:
    return f"{count:,}".replace(",", " ")


@register
class GeneratedSize(Check):
    name = "generated_size"
    source = "general"
    description = "Picture: its pixel count is the fixed budget of an image-generation tool, whatever its shape."

    def applies(self, context: DocumentContext) -> bool:
        record = context.record
        return record.kind in ("picture", "pdf_scan") and any(page.pixel_size for page in record.pages)

    def run(self, context: DocumentContext) -> CheckResult:
        settings = context.settings
        hits = []
        for page in context.record.pages:
            if not page.pixel_size:
                continue
            width, height = page.pixel_size
            for budget in settings.generated_pixel_budgets:
                if abs(width * height - budget) <= settings.generated_size_tolerance * budget:
                    hits.append((page.number, width, height, budget))
                    break
        sizes = [list(page.pixel_size) for page in context.record.pages if page.pixel_size]
        if not hits:
            return self.result("pass", "Taille d'image sans rapport avec le nombre fixe de pixels des outils de "
                               "génération d'images.", fields=["taille de l'image"], details={"sizes": sizes})
        number, width, height, budget = hits[0]
        where = f" (page {number})" if len(context.record.pages) > 1 else ""
        gap = abs(width * height - budget) / budget
        return self.result(
            "fail",
            f"Image de {width} x {height} pixels{where}, soit {_pixels(width * height)} pixels : à {percent(gap, 2)} près "
            f"le nombre fixe de pixels ({_pixels(budget)}) qu'un outil de génération d'images dépense quand il redessine "
            "une image en gardant ses proportions. Un appareil photo donne une forme fixe propre à l'appareil, un scanner "
            "une taille liée au papier et à la résolution ; ici la forme est libre et le nombre de pixels fixe.",
            fields=["taille de l'image"],
            clues=[f"image de {_pixels(width * height)} pixels, budget fixe d'un outil de génération d'images"],
            fraud_type="image générée ou retouchée par IA",
            ai_generated=True,
            details={"sizes": sizes, "hits": [{"page": p, "width": w, "height": h, "budget": b} for p, w, h, b in hits]},
        )
