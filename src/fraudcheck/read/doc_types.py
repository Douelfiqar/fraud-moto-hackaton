"""Field lists and expected elements per document type, read from ``config/doc_types/*.toml``.

Each file names the natures of the organisers' spreadsheet it covers, the fields to
copy (with a kind: text, date, number or code), the elements a genuine document
shows, and which dates may not come after which. The lists come from APRIL's fiches.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from fraudcheck.ingest.catalogue import ConfigError, normalise_text
from fraudcheck.ingest.models import Family

FieldKind = Literal["text", "date", "number", "code"]


class FieldSpec(BaseModel):
    name: str
    label: str
    kind: FieldKind = "text"


class FormLabel(BaseModel):
    """A label, heading or legal mention the form itself prints, in its printed order."""

    text: str
    source: str


class FormZone(BaseModel):
    """A zone a genuine form always shows (photo, signature, machine-readable lines, stamp...)."""

    name: str
    where: str = ""
    source: str


class BaseForm(BaseModel):
    """The base form of a document type: what the form looks like when empty, with a source for each item."""

    description: str
    source: str
    layout: Literal["fixed", "varies"] = Field(
        "varies", description="fixed: one layout set by regulation, so a zone out of place counts; varies: the layout "
        "changes with the issuer or the version, so only the presence of the zones is judged.",
    )
    labels: list[FormLabel] = Field(default_factory=list)
    zones: list[FormZone] = Field(default_factory=list)
    field_order: list[str] = Field(default_factory=list, description="Labels of the fields, in their printed order.")


class DocType(BaseModel):
    name: str
    family: Family
    label: str
    natures: list[str] = Field(min_length=1)
    fields: list[FieldSpec] = Field(min_length=1)
    elements: list[str] = Field(default_factory=list)
    date_order: list[tuple[str, str]] = Field(default_factory=list)
    base_form: BaseForm | None = None

    def field_names(self, kind: FieldKind | None = None) -> list[str]:
        return [spec.name for spec in self.fields if kind is None or spec.kind == kind]


def load_doc_types(folder: Path) -> list[DocType]:
    """Every document type of the folder. Raises ``ConfigError`` for a missing folder or a bad file."""
    files = sorted(folder.glob("*.toml"))
    if not files:
        raise ConfigError(f"no document type file in {folder}")
    types = []
    for path in files:
        try:
            doc_type = DocType.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
            raise ConfigError(f"document type {path.name} could not be read ({exc})") from exc
        names = doc_type.field_names()
        unknown = sorted({name for pair in doc_type.date_order for name in pair} - set(doc_type.field_names("date")))
        if len(set(names)) != len(names) or unknown:
            raise ConfigError(f"{path.name}: duplicate fields, or date_order names non-date fields {unknown}")
        types.append(doc_type)
    return types


def doc_type_for(nature: str | None, types: Sequence[DocType]) -> DocType | None:
    """The type whose natures include this one (compared without accents or case), or ``None``."""
    if not nature:
        return None
    wanted = normalise_text(nature)
    return next((doc_type for doc_type in types if wanted in {normalise_text(n) for n in doc_type.natures}), None)


def doc_type_named(name: str | None, types: Sequence[DocType]) -> DocType | None:
    return next((doc_type for doc_type in types if doc_type.name == name), None)
