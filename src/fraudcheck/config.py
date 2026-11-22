"""Shared pieces of every stage's settings: the repository root, ``.env`` reading, and the loader.

Each stage has one settings model with a prefix for its environment variables
(``INGEST_``, ``CHECK_``, ``READ_``, ``DECIDE_``). Values are taken from, lowest
priority first: the defaults in the model, the ``.env`` file at the repository
root, environment variables, then explicit overrides (command-line options).
Relative paths are read from the repository root.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)

ENV_FILE = ".env"
Model = TypeVar("Model", bound=BaseModel)


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest folder above ``start`` (default: this file) that holds ``pyproject.toml``.

    Falls back to the current directory when the package is installed outside the repository.
    """
    here = (start or Path(__file__)).resolve()
    for folder in here.parents:
        if (folder / "pyproject.toml").is_file():
            return folder
    return Path.cwd().resolve()


def read_dotenv(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from a ``.env`` file. A missing or unreadable file gives nothing."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


class RootedSettings(BaseModel):
    """Base of every stage's settings: knows the repository root and turns paths around it."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    root: Path = Field(default_factory=find_repo_root, description="Repository root.")

    @field_validator("root", mode="after")
    @classmethod
    def _absolute_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def resolve(self, path: Path | str) -> Path:
        """Turn a setting or a path stored in a record into an absolute path."""
        candidate = Path(path).expanduser()
        return candidate if candidate.is_absolute() else self.root / candidate

    def display(self, path: Path | str) -> str:
        """Path as written in output files: relative to the root with ``/``, or absolute when outside."""
        absolute = Path(os.path.abspath(self.resolve(path)))
        try:
            return absolute.relative_to(self.root).as_posix()
        except ValueError:
            return absolute.as_posix()


def values_from_env(
    model: type[BaseModel],
    prefix: str,
    variables: Mapping[str, str],
    aliases: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Keep the variables that name a field of ``model``; warn about unknown ``prefix`` ones (likely typos).

    ``aliases`` maps other variable names to fields, such as ``AZURE_OPENAI_MODEL`` to ``model``.
    """
    known = set(model.model_fields)
    found: dict[str, str] = {}
    for key, value in variables.items():
        upper = key.upper()
        if aliases and upper in aliases:
            found.setdefault(aliases[upper], value)
            continue
        if not upper.startswith(prefix):
            continue
        name = key[len(prefix) :].lower()
        if name in known:
            found[name] = value
        else:
            log.warning("Unknown setting %s ignored", key)
    return found


def build_settings(
    model: type[Model],
    prefix: str,
    overrides: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_dotenv: bool = True,
    aliases: Mapping[str, str] | None = None,
) -> Model:
    """Build ``model`` from its defaults, ``.env``, environment variables and explicit overrides.

    ``overrides`` win over everything; ``None`` values are skipped. Variables named by
    ``prefix`` beat ``aliases``. Raises ``pydantic.ValidationError`` when a value is invalid.
    """
    environ = os.environ if environ is None else environ
    overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
    root_hint = overrides.get("root") or environ.get(prefix + "ROOT")
    root = Path(root_hint).expanduser().resolve() if root_hint else find_repo_root()

    values: dict[str, Any] = {}
    if use_dotenv:
        values.update(values_from_env(model, prefix, read_dotenv(root / ENV_FILE), aliases))
    values.update(values_from_env(model, prefix, environ, aliases))
    values.update(overrides)
    if "root" in model.model_fields:
        values.setdefault("root", root)
    return model(**values)
