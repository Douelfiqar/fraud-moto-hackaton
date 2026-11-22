"""Helpers shared by the checks on the inspection: the base form, matching names, French words.

The model's answer names zones and fields in its own words. Evidence only ever names
what the configuration names (a zone, a label, a field of ``config/doc_types``) or a
page number, so that no text written by the model, and no value of the document, can
reach the evidence. The model's own words stay in ``details``.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from fraudcheck.checks.base import Check, CheckResult, DocumentContext
from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.read.doc_types import BaseForm, DocType, doc_type_for, doc_type_named, load_doc_types

ASPECTS = {
    "font": "police", "size": "taille", "weight": "graisse", "colour": "couleur", "sharpness": "netteté",
    "baseline": "alignement sur la ligne", "spacing": "espacement", "background_patch": "fond rapporté autour du texte",
}
REASONS = {
    "refusal": "refus du modèle", "empty answer": "réponse vide", "content filter": "filtre de contenu",
    "answer cut off": "réponse coupée", "answer did not match the form": "réponse hors du formulaire",
    "no page picture (ingest error)": "pas d'image de page",
}


def key(text: str) -> str:
    """Letters and digits only, lower case, without accents: how names are compared."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


_PLACE_WORDS = re.compile(r"(^| )(pages?|lignes?|p|l) \d+(?= |$)")
_FIELD_WORDS = r"(?:rubriques?|champs?|cases?|mentions?|numeros?|points?)"


def _names_digit(wanted: str, digit: str) -> bool:
    """Does the text designate the field numbered ``digit``? "2 prenoms" and "rubrique 2" do; "4c" and "zone 12" do not."""
    return bool(re.search(rf"^{digit}( |$)", wanted)
                or re.search(rf"(^| ){_FIELD_WORDS} (?:\d+ (?:et|a|ou) )?{digit}( |$)", wanted))


def match(name: str, known: Sequence[str]) -> str | None:
    """The known name that ``name`` designates: the same words, a known name inside it, or it inside one known name.

    Where the model says the thing is ("page 1", "ligne 3") is dropped first: those digits name
    a place, not a field. A field named by one digit ("2.") is matched only when the text says
    so ("2. Prénoms", "rubrique 2"); a field named by one letter is matched only by itself.
    """
    wanted = " ".join(_PLACE_WORDS.sub(" ", key(name)).split())
    if not wanted:
        return None
    keyed = [(candidate, key(candidate)) for candidate in known]
    for candidate, candidate_key in keyed:
        if candidate_key == wanted:
            return candidate
    for candidate, candidate_key in sorted(keyed, key=lambda pair: -len(pair[1])):
        if len(candidate_key) >= 2 and re.search(rf"(^| ){re.escape(candidate_key)}( |$)", wanted):
            return candidate
        if candidate_key.isdigit() and len(candidate_key) == 1 and _names_digit(wanted, candidate_key):
            return candidate
    inside = [candidate for candidate, candidate_key in keyed if len(wanted) >= 4 and wanted in candidate_key]
    return inside[0] if len(inside) == 1 else None


@functools.lru_cache(maxsize=8)
def _types(folder: str) -> tuple[DocType, ...]:
    try:
        return tuple(load_doc_types(Path(folder)))
    except ConfigError:
        return ()


def doc_type(context: DocumentContext) -> DocType | None:
    """The type the document was inspected as, else the type of its nature."""
    types = _types(str(context.settings.doc_types_folder))
    named = doc_type_named(getattr(context.inspection, "doc_type", None), types)
    return named or doc_type_for(context.record.nature, types)


def base_form(context: DocumentContext) -> BaseForm | None:
    kind = doc_type(context)
    return kind.base_form if kind else None


def known_names(context: DocumentContext) -> list[str]:
    """Every name the configuration gives for this type: zones, labels, field order, fields."""
    kind, form = doc_type(context), base_form(context)
    names = [zone.name for zone in form.zones] + [label.text for label in form.labels] + list(form.field_order) if form else []
    return names + ([spec.label for spec in kind.fields] if kind else [])


def real_page(context: DocumentContext, page: int) -> int:
    """The page number when the document has such a page, else 0.

    The number comes from the model: one that is not a page of this document (a year, a piece
    of a number seen on it) must never reach the evidence.
    """
    return page if 1 <= page <= len(context.record.pages) else 0


def place(page: int, name: str | None) -> str:
    """``page`` must already be checked with ``real_page``."""
    return (f"page {page}" if page > 0 else "page non précisée") + (f" (« {name} »)" if name else "")


class InspectionCheck(Check):
    """A check on the document's own inspection; it applies once the document was inspected."""

    def applies(self, context: DocumentContext) -> bool:
        return context.inspection is not None

    def not_usable(self, context: DocumentContext) -> CheckResult | None:
        """``cannot_tell`` when there is nothing to judge: no answer, a stale answer, or nothing visible."""
        inspection = context.inspection
        if inspection.status != "inspected" or inspection.answer is None:
            reason = REASONS.get(inspection.reason or "", "raison inconnue")
            return self.result("cannot_tell", f"L'inspection n'a pas pu se faire ({reason}).")
        if inspection.fingerprint and context.record.fingerprint and inspection.fingerprint != context.record.fingerprint:
            return self.result("cannot_tell", "L'inspection porte sur une autre version du fichier : à refaire.")
        if inspection.answer.readable == "no":
            return self.result("cannot_tell", "Le document ne se voit pas assez sur les images pour être inspecté.")
        form, answer = base_form(context), inspection.answer
        said_nothing = (not (answer.zones or answer.typography or answer.fixed_text_errors or answer.fine_print)
                        and answer.field_order.verdict == "cannot_tell")
        if form and form.zones and said_nothing:
            # The form names mandatory zones and the answer is empty everywhere: the comparison was not done.
            return self.result("cannot_tell", "L'inspection n'a rien décrit du document : réponse vide, à refaire.")
        return None
