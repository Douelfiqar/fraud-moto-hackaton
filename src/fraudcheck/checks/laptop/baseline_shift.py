"""baseline_shift: in one printed line, a piece of text sitting off the line its neighbours sit on.

Letters sit on an invisible line, the baseline. Text typed in one go shares it exactly.
A piece pasted or retyped by hand often lands a fraction of a point higher or lower,
although it has the same size as its neighbours. Pieces of another size (superscripts,
footnote marks) and pieces far apart in the line (another column) are not compared.
"""

from __future__ import annotations

from fraudcheck.checks.base import Check, CheckResult, DocumentContext, percent, register
from fraudcheck.checks.laptop._typography import agreed_value, applies_to, line_neighbours, pieces, places, printed_lines


@register
class BaselineShift(Check):
    name = "baseline_shift"
    source = "general"
    description = "Text PDF: a piece of text whose baseline sits off its neighbours' although it has their size."

    def applies(self, context: DocumentContext) -> bool:
        return applies_to(context)

    def run(self, context: DocumentContext) -> CheckResult:
        settings = context.settings
        found = pieces(context, settings.typo_min_chars, upright_only=True)
        shifted = []
        for line in printed_lines(found, settings.line_min_overlap):
            for piece in line:
                if piece.baseline is None:
                    continue
                same_size = [other for other in line_neighbours(piece, line, settings.line_max_gap_em)
                             if other.baseline is not None
                             and abs(other.span.size - piece.span.size) <= settings.typo_same_size_pt]
                tolerance = settings.baseline_min_shift * piece.size_px / 2
                line_base = agreed_value([other.baseline for other in same_size], tolerance)
                if line_base is None:
                    continue  # the neighbours do not agree on one baseline: nothing to compare with
                shift = abs(piece.baseline - line_base) / piece.size_px
                if settings.baseline_min_shift <= shift <= settings.baseline_max_shift:
                    shifted.append((piece, shift))
        fields = ["texte du PDF"]
        if not found:
            return self.result("cannot_tell", "Aucun texte horizontal à comparer dans le document.", fields=fields)
        if not shifted:
            return self.result("pass", "Dans chaque ligne, les morceaux de texte de même taille partagent la même ligne "
                               "de base.", fields=fields)
        largest = max(shift for _, shift in shifted)
        where = places(context, ((piece.page, piece.span.bbox) for piece, _ in shifted))
        return self.result(
            "fail",
            f"{len(shifted)} morceau(x) de texte décalé(s) par rapport à la ligne de base de leurs voisins de même "
            f"taille (jusqu'à {percent(largest)} de la hauteur des lettres) : {where}.",
            fields=fields,
            clues=["texte décalé par rapport à la ligne"],
            fraud_type="texte ajouté ou retapé",
            details={"pieces": [{"page": p.page, "bbox": list(p.span.bbox), "shift": round(s, 4)} for p, s in shifted]},
        )
