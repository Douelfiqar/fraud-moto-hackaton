"""Every number the checks use, in one place (environment variables ``CHECK_<SETTING>``).

Each default carries its reason in its description; ``docs/checks.md`` lists the checks
that use them.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from fraudcheck.config import RootedSettings, build_settings

ENV_PREFIX = "CHECK_"


class CheckSettings(RootedSettings):
    """Settings of the checks."""

    # font
    fallback_fonts: tuple[str, ...] = Field(
        ("LiberationSans", "LiberationSerif", "LiberationMono"),
        description="Font families editing tools put in when the original font is missing.",
    )
    font_max_share: float = Field(
        0.25, gt=0, lt=1, description="A fallback font above this share of the characters is a main font."
    )

    # typography inside a text PDF (one document, its own text layer)
    typo_min_chars: int = Field(
        2, ge=1, description="Pieces with fewer non-blank characters (bullets, ticks, symbols) are layout marks."
    )
    typo_same_size_pt: float = Field(
        0.05, ge=0, description="Sizes this close, in points, are the same size: PDF writers round sizes."
    )
    line_min_overlap: float = Field(
        0.5, gt=0, le=1, description="Two pieces share a printed line when their heights overlap by this share."
    )
    line_max_gap_em: float = Field(
        6.0, gt=0, description="Neighbours in a line are at most this many letter heights apart; farther is another column."
    )
    baseline_min_shift: float = Field(
        0.05, gt=0, description="Baseline shift, as a share of the letter height, that counts; genuine files show up to 3%."
    )
    baseline_max_shift: float = Field(
        0.35, gt=0, description="Above a third of the letter height, a piece is a superscript, a subscript or another line."
    )
    cover_min_share: float = Field(
        0.6, gt=0, le=1, description="A piece is hidden when this share of its box lies under an opaque shape drawn after it."
    )
    page_picture_share: float = Field(
        0.9, gt=0, le=1, description="A picture this large drawn after the text is a scan over its own text layer: normal."
    )
    overlap_min_share: float = Field(
        0.6, gt=0, le=1, description="Two pieces of text are printed on top of each other when they share this much of the smaller box."
    )

    # generated_size
    generated_pixel_budgets: tuple[int, ...] = Field(
        (1572864,), description="Pixel counts that image-generation tools spend on a picture whatever its shape: "
        "1024 x 1536 = 1,572,864 for the tool known here; one more value covers another tool.",
    )
    generated_size_tolerance: float = Field(
        0.001, gt=0, lt=0.05, description="Share of the budget a picture's pixel count may differ by and still match "
        "(0.1%); a camera or a scanner does not come that close by chance.",
    )

    # inspection checks (what the model observed on the pictures, stage "inspect")
    spelling_min_errors: int = Field(
        1, ge=1, description="Misspellings in the form's own printed text needed to fail: one is already a sign of a "
        "form typed again by hand; raise it if the model sees misspellings that are not there.",
    )

    # rules on the reading (step 2)
    licence_validity_years: int = Field(
        15, ge=1, description="A French card licence (model of 2013) is valid at most this many years for motorbikes "
        "and cars (directive 2006/126/CE, article 7: up to 15 years; France chose 15).",
    )
    licence_validity_tolerance_days: int = Field(
        31, ge=0, description="Days allowed beyond those years, so that a different way of counting days never fails "
        "a genuine card.",
    )
    vin_rule_from: date = Field(
        date(2000, 1, 1), description="Vehicles first registered from this date carry a 17-character VIN without I, O "
        "or Q (ISO 3779, required on two-wheelers by directive 93/34/CEE); older frame numbers can be shorter.",
    )

    releve_max_age_months: int = Field(
        3, ge=1, description="A relevé must have been edited less than this many months before the date of the check "
        "(APRIL's sheet: \"doit dater de moins de 3 mois\").",
    )

    # family checks
    reference_date: date | None = Field(
        None, description="Date against which validity and age are judged (expired documents, old relevés); "
        "default: the day of the run.",
    )
    doc_types_dir: Path = Field(Path("config/doc_types"), description="Field lists per document type (shared with Read).")

    # official_wording (words read on the page by the local text reader)
    official_words_path: Path = Field(
        Path("config/official_words.toml"), description="The long words of each official form's own printed wording."
    )
    wording_min_confidence: float = Field(
        0.85, ge=0, le=1, description="A word counts only when the local reader is this sure of its line; on a "
        "degraded copy the reader's own mistakes come with a lower confidence.",
    )
    wording_min_similarity: float = Field(
        0.7, gt=0, lt=1, description="A printed word is a damaged copy of an official word when it is at least this "
        "close to it (share of matching letters) and about as long.",
    )
    wording_max_length_gap: int = Field(
        3, ge=0, description="Largest difference in letters between a printed word and the official word it is "
        "compared with; farther apart they are two different words.",
    )
    wording_min_wrong_letters: int = Field(
        3, ge=1, description="Letters that must be wrong, after undoing the reader's usual confusions, for a word to "
        "count as damaged. One or two wrong letters are within what a reader does on a genuine page: only reported.",
    )
    twod_doc_max_days: int = Field(
        31, ge=0, description="Days allowed between the day a 2D-Doc was signed and the date of issue printed on the "
        "document. A document is signed when it is issued: a few days apart at most on a genuine document; a "
        "code signed long before the printed date means that date was typed again.",
    )
    wording_languages: tuple[str, ...] = Field(
        ("fr", "en", "es", "pt", "it", "de", "nl"),
        description="Dictionaries a printed word is looked up in before it can count as damaged: official forms "
        "repeat their title in other European languages.",
    )

    # reference lists of the rules on values (one line to add a maker or a country, no code)
    vehicle_makers_path: Path = Field(
        Path("config/vehicle_makers.toml"), description="Maker codes (first three characters of a vehicle "
        "identification number) that name a maker of two-wheelers only, or of cars only.",
    )
    licence_numbers_path: Path = Field(
        Path("config/licence_numbers.toml"), description="Length of the driving licence number in the countries "
        "whose numbering was verified.",
    )

    @property
    def doc_types_folder(self) -> Path:
        return self.resolve(self.doc_types_dir)

    @property
    def official_words_file(self) -> Path:
        return self.resolve(self.official_words_path)

    @property
    def vehicle_makers_file(self) -> Path:
        return self.resolve(self.vehicle_makers_path)

    @property
    def licence_numbers_file(self) -> Path:
        return self.resolve(self.licence_numbers_path)

    @field_validator("fallback_fonts", "generated_pixel_budgets", "wording_languages", mode="before")
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    def today(self) -> date:
        return self.reference_date or date.today()


def load_check_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
) -> CheckSettings:
    return build_settings(CheckSettings, ENV_PREFIX, overrides, environ=environ, use_dotenv=use_dotenv)
