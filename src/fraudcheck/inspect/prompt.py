"""The inspection prompt: ``prompts/inspect_document.md`` filled in for one document.

The file has the same shape as the reading's prompt (a system part, a user part, a
version that is a digest of the whole file), so it is loaded the same way.
"""

from __future__ import annotations

from fraudcheck.read.doc_types import DocType
from fraudcheck.read.prompt import Prompt, load_prompt

__all__ = ["Prompt", "base_form_block", "load_prompt", "user_text"]

NO_FORM = (
    "Type de document inconnu : aucun formulaire de base. Laisse zones et fixed_text_errors vides, réponds "
    "\"cannot_tell\" pour field_order, et décris seulement la typographie et la prise de vue."
)


LAYOUT = {
    "fixed": "Mise en page fixée par la réglementation : une zone hors de sa place est \"displaced\".",
    "varies": "Mise en page propre à chaque émetteur ou à chaque version : ne juge pas la place des zones (n'utilise pas "
              "\"displaced\"), seulement leur présence.",
}


def base_form_block(doc_type: DocType | None) -> str:
    """The base form of the type, as the model reads it: description, printed text, zones, order."""
    if doc_type is None or doc_type.base_form is None:
        return NO_FORM
    form = doc_type.base_form
    lines = [f"Formulaire de base : {doc_type.label}", form.description, LAYOUT[form.layout]]
    if form.labels:
        lines.append("Texte imprimé par le formulaire lui-même, dans l'ordre :")
        lines += [f"- {label.text}" for label in form.labels]
    else:
        lines.append("Texte imprimé par le formulaire : non décrit ici ; ne note une faute que si tu connais ce texte avec "
                     "certitude.")
    if form.zones:
        lines.append("Zones obligatoires (reprends ces noms exacts dans zones) :")
        lines += [f"- {zone.name}" + (f" ; place : {zone.where}" if zone.where else "") for zone in form.zones]
    else:
        lines.append("Zones obligatoires : aucune décrite ; laisse zones vide.")
    if form.field_order:
        lines.append("Ordre des rubriques : " + " ; ".join(form.field_order) + ".")
    else:
        lines.append("Ordre des rubriques : non décrit ; réponds \"cannot_tell\" pour field_order.")
    return "\n".join(lines)


def user_text(prompt: Prompt, nature: str | None, doc_type: DocType | None, pages: int, tiles: int, references: int) -> str:
    extra = (f", puis {references} image(s) de référence (le même formulaire rempli pour une autre personne : compare "
             "le formulaire, jamais les valeurs)") if references else ""
    return prompt.user.substitute(nature=nature or "inconnue", base_form=base_form_block(doc_type), pages=pages,
                                  tiles=tiles, references=extra)
