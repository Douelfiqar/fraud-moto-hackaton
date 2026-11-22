"""Settings of the inspection stage (environment variables ``INSPECT_<SETTING>``).

The connection (endpoint, deployments, retries, timeout, prices) is the reading's
(``READ_`` settings and the ``AZURE_OPENAI_*`` names of ``.env``): both stages go
through the same gateway. Only what the inspection sends is set here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from fraudcheck.config import RootedSettings, build_settings

ENV_PREFIX = "INSPECT_"


class InspectSettings(RootedSettings):
    prompt_path: Path = Field(Path("prompts/inspect_document.md"), description="The prompt file.")
    doc_types_dir: Path = Field(Path("config/doc_types"), description="Base form per document type ([base_form]).")
    templates_dir: Path = Field(
        Path("data/templates"),
        description="Reference pictures, one folder per document type (data/templates/<type>/*.png|jpg); may be "
        "empty or missing. Under data/, never in Git.",
    )
    max_references: int = Field(
        2, ge=0, le=6, description="Reference pictures sent per document, at most: the layout shows on one or two, and "
        "each costs tokens.",
    )
    reference_max_side: int = Field(
        1024, ge=256, description="Reference pictures are shrunk to this many pixels on their longer side: the layout "
        "is what is compared, not the small print.",
    )
    reference_detail: Literal["low", "high", "auto"] = Field(
        "low", description="Detail asked for reference pictures: the layout shows at low detail, which costs least."
    )
    image_detail: Literal["low", "high", "auto"] = Field(
        "high", description="Detail asked for page pictures and zoom tiles: lettering needs high detail."
    )
    tile_rows: int = Field(2, ge=1, le=4, description="Zoom tiles per page, top to bottom.")
    tile_cols: int = Field(2, ge=1, le=4, description="Zoom tiles per page, left to right.")
    tile_overlap: float = Field(
        0.12, ge=0, lt=0.5, description="Share of a tile shared with its neighbour, so a field cut by one tile edge "
        "shows whole in the next tile.",
    )
    max_tiles: int = Field(
        8, ge=0, le=32, description="Zoom tiles per document, at most (pages in order; later pages are sent whole only).",
    )
    max_completion_tokens: int = Field(
        4000, ge=256, description="Upper bound of one answer, in tokens: observations are short (the gateway doubles "
        "it once when an answer is cut off).",
    )

    @property
    def prompt_file(self) -> Path:
        return self.resolve(self.prompt_path)

    @property
    def doc_types_folder(self) -> Path:
        return self.resolve(self.doc_types_dir)

    @property
    def templates_folder(self) -> Path:
        return self.resolve(self.templates_dir)


def load_inspect_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
) -> InspectSettings:
    return build_settings(InspectSettings, ENV_PREFIX, overrides, environ=environ, use_dotenv=use_dotenv)
