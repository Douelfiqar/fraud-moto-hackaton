"""Settings of step 2 (environment variables ``READ_<SETTING>``).

Deployment names and the endpoint come from the team's ``.env`` under the names the
organisers use: ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_MODEL`` (the cheaper
deployment, used by default) and ``AZURE_OPENAI_MODEL_SOL`` (the stronger one).
Secrets (API key or Entra identifiers) are never held here: only the gateway reads them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from fraudcheck.config import RootedSettings, build_settings

ENV_PREFIX = "READ_"
ALIASES = {
    "AZURE_OPENAI_ENDPOINT": "endpoint",
    "AZURE_OPENAI_MODEL": "model",
    "AZURE_OPENAI_MODEL_SOL": "strong_model",
}


class ReadSettings(RootedSettings):
    endpoint: str | None = Field(None, description="Azure OpenAI endpoint (AZURE_OPENAI_ENDPOINT).")
    model: str | None = Field(None, description="Cheaper deployment, used by default (AZURE_OPENAI_MODEL).")
    strong_model: str | None = Field(None, description="Stronger deployment (AZURE_OPENAI_MODEL_SOL).")
    use_strong: bool = Field(False, description="Read with the stronger deployment instead.")
    prompt_path: Path = Field(Path("prompts/read_document.md"), description="The prompt file.")
    doc_types_dir: Path = Field(Path("config/doc_types"), description="Field lists per document type.")
    max_completion_tokens: int = Field(8000, ge=256, description="Upper bound of one answer, in tokens.")
    max_retries: int = Field(4, ge=0, le=10, description="Retries after a rate limit or a passing network error.")
    backoff_seconds: float = Field(2.0, ge=0, description="First wait before a retry; it doubles each time.")
    timeout_seconds: float = Field(180.0, gt=0, description="Longest wait for one answer.")
    image_detail: Literal["low", "high", "auto"] = Field("high", description="Detail level asked for the pictures.")
    max_text_chars: int = Field(15000, ge=0, description="Characters of the PDF's own text sent with the pictures.")
    reread_turned: bool = Field(True, description="Turn sideways pages and read the document once more.")
    second_opinion: bool = Field(
        True, description="When the cheaper deployment gives a value up at its main place while it copied the same "
        "value from another place of the document, ask the stronger deployment once and keep its reading. A decision "
        "then does not depend on the cheaper model giving up on the very value that was typed over.",
    )
    price_input_per_million: float | None = Field(None, ge=0, description="Price of a million input tokens, if known.")
    price_output_per_million: float | None = Field(None, ge=0, description="Price of a million output tokens, if known.")

    @property
    def deployment(self) -> str | None:
        """The deployment used for reading."""
        return self.strong_model if self.use_strong else self.model

    @property
    def prompt_file(self) -> Path:
        return self.resolve(self.prompt_path)

    @property
    def doc_types_folder(self) -> Path:
        return self.resolve(self.doc_types_dir)


def load_read_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
) -> ReadSettings:
    return build_settings(ReadSettings, ENV_PREFIX, overrides, environ=environ, use_dotenv=use_dotenv, aliases=ALIASES)
