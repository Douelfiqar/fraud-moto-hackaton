"""What the page lists before any upload: the document types by family, and the checks by kind.

Both come from the pipeline itself, so the page never falls behind it: the types are
the natures of ``config/doc_types/*.toml``, the checks are the registered ones.
"""

from __future__ import annotations

from pathlib import Path

from fraudcheck.checks.base import registered
from fraudcheck.read.doc_types import load_doc_types

#: Families in the order the page shows them, with the name a claims handler uses.
FAMILY_LABELS = {
    "licence": "Permis de conduire",
    "identity": "Identité et séjour",
    "carte_grise": "Carte grise",
    "releve": "Relevé d'information",
    "rib": "RIB",
}
#: The three kinds of checks, from the package a check lives in.
KIND_OF_PACKAGE = {"laptop": "file", "family": "values", "inspection": "picture"}
KINDS = ("file", "values", "picture")


def type_choices(doc_types_folder: Path) -> list[dict]:
    """The natures a document can be given, grouped by family: ``[{family, label, natures: [{nature, label}]}]``.

    ``nature`` is the exact wording the pipeline expects; ``label`` is the same without
    the family's name in front, for a list already headed by that name.
    """
    by_family: dict[str, list[dict]] = {family: [] for family in FAMILY_LABELS}
    for doc_type in load_doc_types(doc_types_folder):
        for nature in doc_type.natures:
            by_family.setdefault(doc_type.family, []).append(
                {"nature": nature, "label": _short(nature, FAMILY_LABELS.get(doc_type.family, ""))}
            )
    return [
        {"family": family, "label": FAMILY_LABELS.get(family, family), "natures": natures}
        for family, natures in by_family.items() if natures
    ]


def _short(nature: str, family_label: str) -> str:
    rest = nature[len(family_label):].strip(" ,") if family_label and nature.lower().startswith(family_label.lower()) else ""
    if not rest or rest.startswith("("):  # nothing left, or only a remark in brackets: the nature reads better whole
        return nature
    return rest[:1].upper() + rest[1:]


def check_kinds() -> dict[str, str]:
    """Every registered check with its kind: ``file`` (marks left in the file, found on the laptop),
    ``values`` (rules on the values read) or ``picture`` (what was seen on the picture)."""
    return {
        name: KIND_OF_PACKAGE.get(type(check).__module__.split(".")[-2], "values")
        for name, check in registered().items()
    }


def kind_counts() -> dict[str, int]:
    kinds = list(check_kinds().values())
    return {kind: kinds.count(kind) for kind in KINDS}
