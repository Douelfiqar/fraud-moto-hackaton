"""Shapes of a reading: the form the model fills, and what is saved for each document.

The form is sent to Azure as a structured output: the model must fill exactly
these boxes. Every box is required (a missing value is written ``null`` with a
state), which is what the strict mode of structured outputs asks for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldState = Literal["read", "absent", "unreadable"]
ElementState = Literal["present", "absent", "unreadable"]
Readable = Literal["yes", "partly", "no"]
#: Quarter turns, in degrees clockwise, that make a page read normally.
Turn = Literal["0", "90", "180", "270"]


class _Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldValue(_Form):
    name: str = Field(description="Name of the field, exactly as given in the list.")
    value: str | None = Field(description="Value copied character by character as printed; null if absent.")
    state: FieldState
    other_copies: list[str] = Field(
        description="Every other place where the same value is printed, each copied exactly as printed there."
    )


class ElementCheck(_Form):
    name: str = Field(description="Name of the expected element, exactly as given in the list.")
    state: ElementState


class PageTurn(_Form):
    page: int = Field(description="Page number, starting at 1, in the order the pictures were given.")
    turn: Turn = Field(description="Degrees to turn the picture clockwise so that its text reads normally.")


class ReadingAnswer(_Form):
    """The form the model fills for one document."""

    nature_seen: str = Field(description="What the document is, in a few words, as the model sees it.")
    readable: Readable = Field(description="Whether the whole document could be read: yes, partly or no.")
    pages: list[PageTurn]
    fields: list[FieldValue]
    elements: list[ElementCheck]


ReadingStatus = Literal["read", "no_reading"]


class Reading(BaseModel):
    """What is saved in ``cache/<id>/reading.json`` for one document."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: ReadingStatus
    reason: str | None = Field(None, description="Why there is no reading: refusal, empty answer, filter...")
    answer: ReadingAnswer | None = None
    doc_type: str | None = Field(None, description="Document type whose field list was asked.")
    model: str | None = None
    prompt_version: str | None = None
    fingerprint: str | None = None
    turns: dict[int, int] = Field(default_factory=dict, description="Page number to degrees turned before reading.")
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    read_at: datetime | None = None

    def field(self, name: str) -> FieldValue | None:
        """The answer's field of that name, or ``None`` when the model did not return it."""
        if self.answer is None:
            return None
        return next((item for item in self.answer.fields if item.name == name), None)

    @property
    def usable(self) -> bool:
        """True when there is an answer the checks can use (read fully or partly)."""
        return self.status == "read" and self.answer is not None and self.answer.readable != "no"
