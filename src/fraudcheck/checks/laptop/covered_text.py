"""covered_text: text hidden under an opaque shape or picture drawn after it, or text printed on text.

The cover-and-retype edit: a white box is drawn over the old value, and the new value
is typed on top or next to it. The old text is still in the file, under the box.
Ingest records, for each page, the opaque shapes and pictures drawn after some text
and touching it; this check measures how much of each piece of text they hide. A
picture covering nearly the whole page (a scan laid over its own text layer, as
scanners do) is normal and not counted. Two different pieces of text printed on top
of each other also count; a piece drawn again inside a longer one in the same font
(a habit of some PDF writers) does not.
"""

from __future__ import annotations

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, register
from fraudcheck.checks.laptop._typography import applies_to, box_area, pieces, places, shared_area


def _same_text(one: str, two: str) -> str:
    return " ".join(one.split()).casefold() if one else ""


@register
class CoveredText(Check):
    name = "covered_text"
    source = "general"
    description = "Text PDF: text under an opaque shape or picture drawn after it, or two texts printed on each other."

    def applies(self, context: DocumentContext) -> bool:
        return applies_to(context)

    def run(self, context: DocumentContext) -> CheckResult:
        settings = context.settings
        hidden = []
        for piece in pieces(context):
            if piece.span.order is None:
                continue
            area = box_area(piece.span.bbox) or 1e-6
            later = [cover for cover in context.text_layer.covers
                     if cover.page == piece.page and cover.order > piece.span.order
                     and shared_area(piece.span.bbox, cover.bbox) / area >= settings.cover_min_share]
            if any(cover.page_share >= settings.page_picture_share for cover in later):
                continue  # under a scan laid over the page: the whole text layer is hidden, as scanners do
            if later:
                hidden.append(piece)
        stacked = self._stacked(pieces(context, settings.typo_min_chars), settings.overlap_min_share)
        fields = ["texte du PDF"]
        if not hidden and not stacked:
            return self.result("pass", "Aucun texte caché sous un aplat ou une image posés après lui, et aucun texte "
                               "imprimé sur un autre.", fields=fields)
        parts, clues = [], []
        if hidden:
            parts.append(f"{len(hidden)} morceau(x) de texte caché(s) sous un aplat opaque ou une image posés après eux "
                         f"({places(context, ((p.page, p.span.bbox) for p in hidden))})")
            clues.append("texte masqué par un aplat posé après lui")
        if stacked:
            parts.append(f"{len(stacked)} paire(s) de textes différents imprimés l'un sur l'autre "
                         f"({places(context, ((a.page, a.span.bbox) for a, _ in stacked))})")
            clues.append("deux textes superposés")
        return self.result(
            "fail",
            "Signe de masquage puis de réécriture : " + " ; ".join(parts) + ".",
            fields=fields,
            clues=clues,
            fraud_type="texte masqué puis réécrit",
            details={
                "hidden": [{"page": p.page, "bbox": list(p.span.bbox)} for p in hidden],
                "stacked": [{"page": a.page, "bbox": list(a.span.bbox)} for a, _ in stacked],
            },
        )

    @staticmethod
    def _stacked(found, min_share: float):
        pairs = []
        for position, one in enumerate(found):
            for two in found[position + 1:]:
                if two.page != one.page:
                    continue
                shared = shared_area(one.span.bbox, two.span.bbox)
                smaller = min(box_area(one.span.bbox), box_area(two.span.bbox)) or 1e-6
                if shared / smaller < min_share:
                    continue
                first, second = _same_text(one.span.text, ""), _same_text(two.span.text, "")
                if first == second:
                    continue  # the same text drawn twice (shadow, bold effect)
                short, long_ = sorted((first, second), key=len)
                if one.span.font == two.span.font and short in long_:
                    continue  # a piece redrawn inside a longer one by the PDF writer
                pairs.append((one, two))
        return pairs
