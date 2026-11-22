"""The prompt of step 2: read from ``prompts/read_document.md`` and filled in for one document."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template

from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.models import TextLayer
from fraudcheck.read.doc_types import DocType

SYSTEM_MARK = "=== system ==="
USER_MARK = "=== user ==="
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ROW_PX = 8  # pieces of text whose middles are this close in height are on the same line


@dataclass(frozen=True)
class Prompt:
    system: str
    user: Template
    version: str


def load_prompt(path: Path) -> Prompt:
    """Read the prompt file. Its version is a digest of the whole file, so any edit is noticed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"prompt not found: {path}") from exc
    body = _COMMENT.sub("", text)
    if SYSTEM_MARK not in body or USER_MARK not in body:
        raise ConfigError(f"{path} needs a '{SYSTEM_MARK}' part and a '{USER_MARK}' part")
    system, user = body.split(SYSTEM_MARK, 1)[1].split(USER_MARK, 1)
    return Prompt(system.strip(), Template(user.strip()), hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])


def fields_block(doc_type: DocType) -> str:
    lines = [f"Type de document : {doc_type.label}", "Champs à recopier (nom exact du champ : ce qu'il désigne) :"]
    lines += [f"- {spec.name} : {spec.label}" for spec in doc_type.fields]
    if doc_type.elements:
        lines.append("Éléments attendus (nom exact de l'élément) :")
        lines += [f"- {element}" for element in doc_type.elements]
    return "\n".join(lines)


def unknown_block(types: Sequence[DocType]) -> str:
    lines = [
        "La nature du document n'est pas connue. Dis d'abord ce qu'il est (nature_seen). Ensuite, s'il correspond "
        "à l'un des types ci-dessous, remplis les champs et les éléments de ce type ; sinon, laisse fields et "
        "elements vides.",
    ]
    for doc_type in types:
        fields = ", ".join(f"{spec.name} ({spec.label})" for spec in doc_type.fields)
        elements = "; ".join(doc_type.elements)
        lines.append(f"- Type « {doc_type.label} » : champs {fields}." + (f" Éléments : {elements}." if elements else ""))
    return "\n".join(lines)


def pdf_text_block(layer: TextLayer | None, max_chars: int) -> str:
    """The PDF's own text, line by line and page by page, cut at ``max_chars``."""
    if layer is None or not layer.spans or max_chars <= 0:
        return "Le fichier ne contient pas de texte réel : lis les images."
    rows: dict[tuple[int, int], list[tuple[float, str]]] = {}
    for span in layer.spans:
        if not span.visible:
            continue
        middle = (span.bbox[1] + span.bbox[3]) / 2
        rows.setdefault((span.page, round(middle / _ROW_PX)), []).append((span.bbox[0], span.text))
    lines, page = [], None
    for (number, _), pieces in sorted(rows.items()):
        if number != page:
            lines.append(f"--- page {number} ---")
            page = number
        lines.append(" ".join(text.strip() for _, text in sorted(pieces) if text.strip()))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... texte coupé ...]"
    return "Texte contenu dans le fichier PDF (aide à la lecture ; l'image fait foi) :\n" + text


def user_text(
    prompt: Prompt,
    nature: str | None,
    doc_type: DocType | None,
    types: Sequence[DocType],
    layer: TextLayer | None,
    pages: int,
    max_chars: int,
) -> str:
    return prompt.user.substitute(
        nature=nature or "inconnue",
        fields=fields_block(doc_type) if doc_type else unknown_block(types),
        pdf_text=pdf_text_block(layer, max_chars),
        pages=pages,
    )
