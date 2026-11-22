"""font: pieces of a text PDF set in the font an editing tool puts in when the original font is missing.

When someone edits a PDF with a tool that does not have the document's font,
the tool writes the new text in a fallback font, most often LiberationSans.
The rest of the document keeps its own fonts. So a fallback font carrying a
small share of the characters, next to other fonts, betrays an edit. A
document written entirely in that font was simply produced with free software.
Ordinary small fonts (OCR-B, Courier, symbols, bold or italic variants) are not
fallback fonts and never fail.
"""

from __future__ import annotations

import re
from collections import Counter

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, percent, register


def _squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_fallback(font: str, families: tuple[str, ...]) -> bool:
    """True for any name variant of a fallback family: 'LiberationSans-Bold', 'Liberation Sans Regular'..."""
    squashed = _squash(font)
    return any(squashed.startswith(_squash(family)) for family in families if family)


@register
class FallbackFont(Check):
    name = "font"
    source = "general"
    description = "Text PDF: pieces set in an editing tool's fallback font, next to the document's own fonts."

    def applies(self, context: DocumentContext) -> bool:
        return context.record.kind == "pdf_text" and bool(context.record.fonts)

    def run(self, context: DocumentContext) -> CheckResult:
        fonts = context.record.fonts or {}
        total = sum(fonts.values())
        fallback = {font: chars for font, chars in fonts.items() if is_fallback(font, context.settings.fallback_fonts)}
        others = [font for font in fonts if font not in fallback]
        fields = ["texte du PDF"]
        if not fallback:
            return self.result(
                "pass",
                "Aucune police de substitution d'outil d'édition parmi les polices du document.",
                fields=fields,
                details={"fonts": len(fonts)},
            )
        fallback_chars = sum(fallback.values())
        share = fallback_chars / total if total else 1.0
        names = ", ".join(sorted(fallback))
        if not others or share > context.settings.font_max_share:
            return self.result(
                "pass",
                f"{names} est une police principale du document ({percent(share)} du texte) : "
                "document produit avec un logiciel libre, pas un signe de retouche.",
                fields=fields,
                details={"fallback_share": round(share, 4)},
            )
        pages, pieces, boxes = self._where(context, fallback)
        main = ", ".join(font for font, _ in Counter({f: fonts[f] for f in others}).most_common(3))
        where = f", page{'s' if len(pages) > 1 else ''} {', '.join(map(str, pages))}" if pages else ""
        return self.result(
            "fail",
            f"Des passages ({pieces or 'plusieurs'} morceaux de texte, {fallback_chars} caractères, soit "
            f"{percent(share)} du texte{where}) sont composés en {names}, police que les logiciels d'édition "
            f"de PDF substituent quand la police d'origine manque ; le reste du document utilise {main}.",
            fields=fields,
            clues=[f"police {names} sur {percent(share)} du texte", "police de substitution d'un outil d'édition"],
            fraud_type="texte modifié (police substituée)",
            details={
                "fallback_fonts": fallback,
                "fallback_share": round(share, 4),
                "pages": pages,
                "pieces": pieces,
                "boxes": boxes,
                "main_fonts": [font for font, _ in Counter({f: fonts[f] for f in others}).most_common(5)],
            },
        )

    @staticmethod
    def _where(context: DocumentContext, fallback: dict[str, int]) -> tuple[list[int], int, list[dict]]:
        """Pages and positions of the fallback pieces, from the text layer (no text is kept)."""
        layer = context.text_layer
        if layer is None:
            return [], 0, []
        spans = [span for span in layer.spans if span.font in fallback]
        boxes = [{"page": span.page, "bbox": list(span.bbox)} for span in spans]
        return sorted({span.page for span in spans}), len(spans), boxes
