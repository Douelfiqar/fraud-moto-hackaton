"""The contract every check follows, and the registry that lists checks by name.

A check answers one narrow question about ONE document and never says "fake".
It returns a ``CheckResult``: ``pass``, ``fail`` or ``cannot_tell`` (or a number
for a score), its evidence in plain words, the fields involved, and where its
rule comes from (``fiche``: APRIL's practice sheets; ``general``: general knowledge).

Each document is judged on its own. A check receives only that
document's ``DocumentContext``: its own record, its own text layer, its own reading,
its own inspection, and the settings. It is never given the cache folder, the index or another
document, and ``tests/test_single_document.py`` fails if a check module tries to
reach them.

Checks register themselves with ``@register``. Every module placed in
``fraudcheck/checks/laptop/``, ``fraudcheck/checks/family/`` or
``fraudcheck/checks/inspection/`` is loaded automatically, so adding or removing a
check touches no other file.
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraudcheck.checks.settings import CheckSettings
from fraudcheck.ingest.models import DocumentRecord, TextLayer
from fraudcheck.read.models import Reading

Outcome = Literal["pass", "fail", "cannot_tell"]
RuleSource = Literal["fiche", "general"]

#: Packages whose modules hold checks; each is imported on first use of the registry.
CHECK_PACKAGES = ("fraudcheck.checks.laptop", "fraudcheck.checks.family", "fraudcheck.checks.inspection")


class CheckResult(BaseModel):
    """One check's answer for one document.

    ``evidence`` and ``clues`` are written in French because they feed the answer
    file. They never quote a value copied from the document
    (names, numbers, addresses); such values may only sit in ``details``.
    """

    model_config = ConfigDict(extra="forbid")

    check: str
    outcome: Outcome | None = None
    score: float | None = None
    evidence: str
    fields: list[str] = Field(default_factory=list)
    source: RuleSource
    clues: list[str] = Field(default_factory=list, description="Short phrases for the answer file's 'indices'.")
    fraud_type: str | None = Field(None, description="Short label for the answer file's optional 'type'.")
    ai_generated: bool = Field(False, description="The failure points to a picture made or redrawn by an AI tool "
                               "(the answer file's optional 'genere_ia').")
    details: dict[str, Any] = Field(default_factory=dict, description="Machine-readable facts; never printed.")

    @model_validator(mode="after")
    def _has_an_answer(self) -> CheckResult:
        if self.outcome is None and self.score is None:
            raise ValueError("a check result needs an outcome or a score")
        return self

    @property
    def cell(self) -> str:
        """What the check table shows for this result."""
        if self.outcome is not None:
            return self.outcome
        return f"{self.score:.3f}"


@dataclass(frozen=True)
class DocumentContext:
    """Everything a check may look at, all of it about one document only."""

    record: DocumentRecord
    reading: Reading | None
    text_layer: TextLayer | None
    settings: CheckSettings
    inspection: Any = None  # the document's own inspection (stage "inspect"), when it ran

    @property
    def id(self) -> str:
        return self.record.id


class Check(ABC):
    """A check that looks at one document."""

    name: ClassVar[str]
    source: ClassVar[RuleSource]
    description: ClassVar[str]

    def applies(self, context: DocumentContext) -> bool:
        """False when the question makes no sense for this document; its table cell stays empty."""
        return True

    @abstractmethod
    def run(self, context: DocumentContext) -> CheckResult: ...

    def result(self, outcome: Outcome, evidence: str, **extra: Any) -> CheckResult:
        source = extra.pop("source", self.source)
        return CheckResult(check=self.name, outcome=outcome, evidence=evidence, source=source, **extra)


_REGISTRY: dict[str, Check] = {}
_LOADED = False


def register(cls: type[Check]) -> type[Check]:
    """Class decorator: make the check available under its ``name``."""
    if cls.name in _REGISTRY and type(_REGISTRY[cls.name]) is not cls:
        raise ValueError(f"two checks are named {cls.name!r}")
    _REGISTRY[cls.name] = cls()
    return cls


def load_check_modules() -> None:
    """Import every module of the check packages, which registers their checks."""
    global _LOADED
    if _LOADED:
        return
    for package_name in CHECK_PACKAGES:
        package = importlib.import_module(package_name)
        for module in pkgutil.iter_modules(package.__path__):
            if not module.name.startswith("_"):
                importlib.import_module(f"{package_name}.{module.name}")
    _LOADED = True


def registered() -> dict[str, Check]:
    """All checks, by name, in alphabetical order."""
    load_check_modules()
    return dict(sorted(_REGISTRY.items()))


def percent(share: float, digits: int = 1) -> str:
    """French percentage: 0.025 becomes '2,5 %'."""
    return f"{share * 100:.{digits}f} %".replace(".", ",")
