"""Shapes of an inspection: the form the model fills, and what is saved for each document.

Every box is required (strict structured output). Observations name a page and a zone
or field; they never quote the person's own values (names, addresses, numbers).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# not_applicable: the zone belongs to the other face (recto or verso) or to another version of the form.
ZoneState = Literal["present", "absent", "displaced", "unreadable", "not_applicable"]
Aspect = Literal["font", "size", "weight", "colour", "sharpness", "baseline", "spacing", "background_patch"]
Strength = Literal["clear", "slight"]
Capture = Literal["photo_of_paper", "scan", "screen_capture", "photo_of_screen", "digital_document", "synthetic_image",
                  "cannot_tell"]
FinePrintState = Literal["crisp", "smeared", "garbled", "absent", "cannot_tell"]
OrderVerdict = Literal["in_order", "out_of_order", "cannot_tell"]


class _Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ZoneObservation(_Form):
    name: str = Field(description="Name of a mandatory zone of the base form, exactly as given in the request.")
    state: ZoneState
    page: int = Field(description="Page where the zone is (or should be), starting at 1; 0 when not found.")


class FixedTextError(_Form):
    page: int
    zone: str = Field(description="Which label, heading or printed mention of the form.")
    printed: str = Field(description="The form's own printed text, as printed (never a personal value).")
    expected: str = Field(description="What the base form says there.")


class FieldOrder(_Form):
    verdict: OrderVerdict
    swapped: list[str] = Field(description="Names of the fields that come out of the base form's order.")


class TypographyObservation(_Form):
    page: int
    zone: str = Field(description="Which zone or field, by its label or position (never its content).")
    aspects: list[Aspect]
    strength: Strength
    description: str = Field(description="What differs, in a short sentence, without quoting a value.")


class FinePrint(_Form):
    """Small print and fine security patterns (perforated or embossed numbers, guilloche lines, microprint)."""

    page: int
    zone: str = Field(description="Which pattern or small print, by its place (never its content).")
    state: FinePrintState


class InspectionAnswer(_Form):
    """The form the model fills when it inspects one document against the base form of its type."""

    zones: list[ZoneObservation]
    fixed_text_errors: list[FixedTextError]
    field_order: FieldOrder
    typography: list[TypographyObservation]
    fine_print: list[FinePrint]
    capture: Capture
    readable: Literal["yes", "partly", "no"]


class Inspection(BaseModel):
    """What is saved in ``cache/<id>/inspection.json`` for one document."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["inspected", "no_inspection"]
    reason: str | None = None
    answer: InspectionAnswer | None = None
    doc_type: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    fingerprint: str | None = None
    turns: dict[int, int] = Field(default_factory=dict, description="Page number to degrees turned before inspecting.")
    tiles: int = 0
    references: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    inspected_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.status == "inspected" and self.answer is not None and self.answer.readable != "no"
