"""fixed_text_spelling: the form's own printed text (headings, labels, legal mentions) has no misspelling.

A genuine form is printed from a proofread master. A misspelled heading is a sign of a
form typed again by hand. Only the form's own text counts, never a filled-in value. A
"misspelling" that differs from the expected text only by accents, case, spaces or
punctuation is dropped here, whatever the model said.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.inspection._inspection import (
    InspectionCheck,
    base_form,
    key,
    known_names,
    match,
    place,
    real_page,
)


_MIN_LIKENESS = 0.6  # a mistake keeps most of the word: "PERMIS DE CONDIURE" is close to "PERMIS DE CONDUIRE"


def _letters(text: str) -> str:
    return key(text).replace(" ", "")


def _is_a_mistake(printed: str, expected: str) -> bool:
    """A mistake is two texts that are both there, differ in their letters, and still look alike.

    Dropped whatever the model said: a difference of accents, case, spaces or punctuation only;
    an entry without the expected text; another wording or another language ("DRIVING LICENCE"
    for "PERMIS DE CONDUIRE"); a label given with its caption against the label alone.
    """
    one, two = _letters(printed), _letters(expected)
    if not one or not two or one == two:
        return False
    return SequenceMatcher(None, one, two).ratio() >= _MIN_LIKENESS


@register
class FixedTextSpelling(InspectionCheck):
    name = "fixed_text_spelling"
    source = "general"
    description = "Inspection: the form's own printed text (headings, labels, legal mentions) has no misspelling."

    def applies(self, context: DocumentContext) -> bool:
        return super().applies(context) and base_form(context) is not None

    def run(self, context: DocumentContext) -> CheckResult:
        if blocked := self.not_usable(context):
            return blocked
        answer, known = context.inspection.answer, known_names(context)
        errors = [error for error in answer.fixed_text_errors if _is_a_mistake(error.printed, error.expected)]
        details = {
            "errors": [{"page": e.page, "zone": e.zone, "printed": e.printed, "expected": e.expected} for e in errors],
            "dropped": len(answer.fixed_text_errors) - len(errors),
            "capture": answer.capture,
        }
        threshold = context.settings.spelling_min_errors
        if len(errors) >= threshold:
            named = [match(error.zone, known) or match(error.expected, known) for error in errors]
            where = " ; ".join(place(real_page(context, error.page), name) for error, name in zip(errors, named, strict=True))
            return self.result(
                "fail",
                f"{len(errors)} faute(s) dans le texte que le formulaire imprime lui-même : {where}.",
                fields=sorted({name for name in named if name}),
                clues=["faute dans le texte imprimé du formulaire"],
                fraud_type="formulaire refait",
                details=details,
            )
        evidence = ("Aucune faute vue dans le texte imprimé par le formulaire." if not errors
                    else f"{len(errors)} faute(s) vue(s), sous le seuil de {threshold}.")
        if answer.readable == "partly":
            evidence += " Une partie du document ne se voit pas."
        return self.result("pass", evidence, details=details)
