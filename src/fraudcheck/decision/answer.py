"""The answer file, in the organisers' TOML format, checked by reading it back with ``tomllib``.

One ``[[document]]`` block per row of the spreadsheet, with ``id`` and ``frauduleux``;
flagged documents also get ``confiance``, ``justification``, ``indices`` and ``type``, and
``genere_ia = true`` when a failed trusted check points to a picture made by an AI tool.
The file is written by hand (TOML is simple) so no extra library is needed.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fraudcheck.decision.decide import Decision
from fraudcheck.decision.settings import DecisionSettings
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_text

FOLDER = "answer"
CASE = "fraude"
_GIT_TIMEOUT_S = 5


class AnswerFileError(ValueError):
    """The answer file read back does not match what was meant to be written."""


@dataclass
class AnswerFile:
    path: Path
    flagged: int
    documents: int
    commit: str | None
    dirty: bool


def toml_string(value: str) -> str:
    """A TOML basic string: quotes, backslashes and control characters escaped."""
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _array(values: Sequence[str]) -> str:
    return "[" + ", ".join(toml_string(value) for value in values) + "]"


def current_commit(root: Path) -> tuple[str | None, bool]:
    """Short SHA of the checked-out commit, and whether files differ from it (``None`` without Git)."""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=_GIT_TIMEOUT_S, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, text=True, timeout=_GIT_TIMEOUT_S, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None, False
    return sha or None, bool(status.strip())


def build_answer(decisions: Sequence[Decision], team: str, stage: str, commit: str | None, models: Sequence[str]) -> str:
    lines = [
        "# Réponses du cas 1 (fraude documentaire), produites par python -m fraudcheck decide",
        "[meta]",
        f"equipe = {toml_string(team)}",
        f"cas = {toml_string(CASE)}",
        f"etape = {toml_string(stage)}",
    ]
    if commit:
        lines.append(f"commit = {toml_string(commit)}")
    if models:
        lines.append(f"modeles = {_array(models)}")
    for decision in decisions:
        lines += ["", "[[document]]", f"id = {toml_string(decision.id)}", f"frauduleux = {'true' if decision.flagged else 'false'}"]
        if decision.flagged and decision.generated:
            lines.append("genere_ia = true")  # optional field: written only when a trusted check says so
        if decision.flagged:
            if decision.fraud_type:
                lines.append(f"type = {toml_string(decision.fraud_type)}")
            lines.append(f"confiance = {round(decision.confidence or 0.5, 2)}")
            lines.append(f"justification = {toml_string(decision.justification or '')}")
            lines.append(f"indices = {_array(decision.clues)}")
    return "\n".join(lines) + "\n"


def validate_answer(text: str, decisions: Sequence[Decision], team: str, stage: str) -> dict:
    """Read the text back with ``tomllib`` and check it says exactly what the decisions say."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise AnswerFileError(f"the answer file is not valid TOML: {exc}") from exc
    meta = data.get("meta", {})
    if meta.get("equipe") != team or meta.get("cas") != CASE or meta.get("etape") != stage:
        raise AnswerFileError("the [meta] block does not hold the expected team, case and stage")
    documents = data.get("document", [])
    if [item.get("id") for item in documents] != [decision.id for decision in decisions]:
        raise AnswerFileError("the documents of the answer file do not match the decisions")
    for item, decision in zip(documents, decisions, strict=True):
        if item.get("frauduleux") is not decision.flagged:
            raise AnswerFileError(f"{decision.id}: 'frauduleux' does not match the decision")
        if item.get("genere_ia", False) is not (decision.flagged and decision.generated):
            raise AnswerFileError(f"{decision.id}: 'genere_ia' does not match the decision")
        if decision.flagged:
            if not isinstance(item.get("confiance"), float) or not 0 <= item["confiance"] <= 1:
                raise AnswerFileError(f"{decision.id}: 'confiance' must be a number between 0 and 1")
            if not item.get("justification") or not isinstance(item.get("indices"), list):
                raise AnswerFileError(f"{decision.id}: a flagged document needs a justification and clues")
    return data


def answer_name(team: str) -> str:
    """``equipe_cas.toml``, as the organisers ask: team06_fraude.toml."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", team).strip("_").lower() or "equipe"
    return f"{slug}_{CASE}.toml"


def write_answer(
    ingest: IngestSettings, settings: DecisionSettings, decisions: Sequence[Decision], models: Sequence[str]
) -> AnswerFile:
    commit, dirty = (settings.commit, False) if settings.commit else current_commit(settings.root)
    text = build_answer(decisions, settings.team, settings.stage, commit, models)
    validate_answer(text, decisions, settings.team, settings.stage)
    path = ingest.cache_path / FOLDER / answer_name(settings.team)
    atomic_write_text(path, text)
    return AnswerFile(path, sum(decision.flagged for decision in decisions), len(decisions), commit, dirty)
