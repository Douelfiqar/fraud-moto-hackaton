"""Settings of the decision, the answer file and the evaluation (environment variables ``DECIDE_<SETTING>``)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from fraudcheck.config import RootedSettings, build_settings

ENV_PREFIX = "DECIDE_"


class DecisionSettings(RootedSettings):
    decision_path: Path = Field(Path("config/decision.toml"), description="Trusted checks and their confidence.")
    team: str = Field("team06", min_length=1, description="Exact team name, written in the answer file.")
    stage: Literal["intermediaire", "final"] = Field("intermediaire", description="Deposit stage ('etape').")
    commit: str | None = Field(None, description="Commit written in the answer file; default: the current one.")
    marked_examples_path: Path = Field(
        Path("data/marked_examples.csv"), description="Marked examples: id and fake or genuine."
    )
    evaluated_documents: int = Field(
        68, ge=1, description="Documents in the scored part of the set, for the score estimate."
    )
    review_decisions_path: Path = Field(
        Path("data/review_decisions.csv"),
        description="Decisions of a person who examined a document: id, decision (faux or vrai), reason. They "
        "replace the automatic decision for that document. Optional file.",
    )

    @property
    def decision_file(self) -> Path:
        return self.resolve(self.decision_path)

    @property
    def review_decisions_file(self) -> Path:
        return self.resolve(self.review_decisions_path)

    @property
    def marked_examples_file(self) -> Path:
        return self.resolve(self.marked_examples_path)


def load_decision_settings(
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
) -> DecisionSettings:
    return build_settings(DecisionSettings, ENV_PREFIX, overrides, environ=environ, use_dotenv=use_dotenv)
