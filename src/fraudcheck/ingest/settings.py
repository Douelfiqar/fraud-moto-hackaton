"""Every number and path that Ingest uses, in one place.

Values are taken from, lowest priority first:

1. the defaults written below,
2. a ``.env`` file at the repository root (only its ``INGEST_*`` lines are read),
3. environment variables named ``INGEST_<SETTING>``, for example ``INGEST_PDF_DPI=300``,
4. options given on the command line.

Relative paths are read from the repository root, so the command gives the same
result from any current directory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from fraudcheck.config import RootedSettings, build_settings, find_repo_root, read_dotenv, values_from_env
from fraudcheck.ingest.models import INGEST_VERSION

__all__ = ["ENV_PREFIX", "IngestSettings", "find_repo_root", "load_settings", "read_dotenv", "settings_from_env"]

ENV_PREFIX = "INGEST_"

#: Settings that change what Ingest writes. Changing any of them makes cached records stale.
OUTPUT_SETTINGS = (
    "pdf_dpi",
    "max_side_px",
    "max_bytes",
    "jpeg_quality",
    "reader_formats",
    "text_min_chars",
    "upright_min_share",
    "upright_max_angle_deg",
    "max_pages",
    "max_open_megapixels",
)

_FORMAT_ALIASES = {"jpg": "jpeg", "tif": "tiff"}


class IngestSettings(RootedSettings):
    """Settings of step 1. Each field says what it controls and why its default."""

    # Where things are.
    documents_dir: Path = Field(Path("data/documents/documents"), description="Documents as received.")
    catalogue_path: Path = Field(
        Path("data/documents_fraude.xlsx"), description="Organisers' spreadsheet with each document's nature."
    )
    catalogue_sheet: str = Field("Documents", description="Sheet of the spreadsheet to read.")
    families_path: Path = Field(Path("config/families.toml"), description="Rules from nature to family.")
    cache_dir: Path = Field(Path("cache"), description="Where records, pictures and the index go.")

    # Pictures handed to the reader.
    pdf_dpi: int = Field(200, ge=50, le=600, description="Pixels per inch of paper when drawing PDF pages.")
    max_side_px: int = Field(
        3000, ge=256, le=20000, description="Longest side, in pixels, of a picture handed to the reader."
    )
    max_bytes: int = Field(5_000_000, ge=10_000, description="Heaviest picture file handed to the reader.")
    jpeg_quality: int = Field(90, ge=50, le=100, description="JPEG quality of working copies.")
    reader_formats: tuple[str, ...] = Field(
        ("png", "jpeg", "webp", "gif"), description="Picture formats the reader accepts as they are."
    )

    # PDF text.
    text_min_chars: int = Field(
        20, ge=0, description="A PDF needs more non-blank characters than this to count as pdf_text."
    )
    upright_min_share: float = Field(
        0.9, gt=0.5, le=1.0, description="Share of a page's characters that must run left to right."
    )
    upright_max_angle_deg: float = Field(
        10.0, ge=0.0, le=44.0, description="Largest tilt, in degrees, of a line still counted as horizontal."
    )

    # Local readers.
    local_barcodes: bool = Field(
        True,
        description="Read barcodes and two-dimensional codes on each page with the local reader, when zxing-cpp is "
        "installed. Not part of the output key: after changing it, run ingest with --force.",
    )
    local_words: bool = Field(
        True,
        description="Read the printed words of each page with the local text reader, when RapidOCR is installed. "
        "Not part of the output key: after changing it, run ingest with --force.",
    )
    local_words_families: tuple[str, ...] = Field(
        ("carte_grise", "licence", "identity"),
        description="Families whose pages the local text reader reads: those printed on an official form, whose "
        "wording is the same on every copy. Reading a page takes a few seconds.",
    )

    # Safety limits.
    max_pages: int = Field(50, ge=1, description="Pages (or TIFF frames) handled per document.")
    max_open_megapixels: float = Field(
        200.0, gt=0, description="Pictures larger than this are refused instead of opened."
    )

    @field_validator("reader_formats", mode="before")
    @classmethod
    def _split_formats(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [part for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            names = (str(part).strip().lower().lstrip(".") for part in value)
            return tuple(_FORMAT_ALIASES.get(name, name) for name in names)
        return value

    @property
    def documents_path(self) -> Path:
        return self.resolve(self.documents_dir)

    @property
    def catalogue_file(self) -> Path:
        return self.resolve(self.catalogue_path)

    @property
    def families_file(self) -> Path:
        return self.resolve(self.families_path)

    @property
    def cache_path(self) -> Path:
        return self.resolve(self.cache_dir)

    def output_key(self) -> str:
        """Short digest of everything that changes the outputs: the output settings and the code version."""
        payload: dict[str, Any] = {name: getattr(self, name) for name in OUTPUT_SETTINGS}
        payload["ingest_version"] = INGEST_VERSION
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def settings_from_env(variables: Mapping[str, str]) -> dict[str, str]:
    """Keep the ``INGEST_*`` variables that name a setting; warn about the others (likely typos)."""
    return values_from_env(IngestSettings, ENV_PREFIX, variables)


def load_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
) -> IngestSettings:
    """Build the settings from the defaults, ``.env``, ``INGEST_*`` variables and explicit overrides.

    ``overrides`` (command-line options) win over everything; ``None`` values are skipped.
    Raises ``pydantic.ValidationError`` when a value is invalid.
    """
    return build_settings(IngestSettings, ENV_PREFIX, overrides, environ=environ, use_dotenv=use_dotenv)
