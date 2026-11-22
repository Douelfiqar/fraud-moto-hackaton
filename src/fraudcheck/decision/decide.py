"""The decision for each document, in three lines tried in order:

1. a trusted check failed: flag the document, with that check's evidence;
2. the document could not be read: do not flag it, and put it on the review list;
3. otherwise: do not flag it.

Which checks are trusted is ``config/decision.toml``. Every other failed check is
reported, with its evidence, on the review list, but never flags. Each document is
decided from its own check results and its own reading only.

A person has the last word: when someone examined a document and wrote their decision
in the review file (``data/review_decisions.csv``: id, faux or vrai, reason), that
decision replaces the automatic one, and the answer file says a person decided.
Outputs: ``cache/decision/decisions.json`` and ``cache/decision/review.csv``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import tomllib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from fraudcheck.checks.base import CheckResult, registered
from fraudcheck.checks.runner import Results, load_results
from fraudcheck.decision.settings import DecisionSettings
from fraudcheck.ingest.catalogue import ConfigError, load_catalogue
from fraudcheck.ingest.discover import DUPLICATE_MARK, natural_key
from fraudcheck.ingest.models import DocumentRecord
from fraudcheck.ingest.pipeline import load_index, load_record
from fraudcheck.ingest.settings import IngestSettings
from fraudcheck.ingest.storage import atomic_write_text
from fraudcheck.read.models import Reading
from fraudcheck.read.store import load_reading

log = logging.getLogger(__name__)

FOLDER = "decision"
DECISIONS_NAME = "decisions.json"
REVIEW_NAME = "review.csv"
_GENERIC_JUSTIFICATION = "Contrôle automatique en échec ; détails dans le dossier d'analyse de l'équipe."
_MIN_LEAK_LENGTH = 4
#: Confidence written for a document a person examined and judged fake.
REVIEW_CONFIDENCE = 0.9
_REVIEW_CLUE = "document examiné par une personne"
_FAKE_WORDS = frozenset({"faux", "fake", "fraude", "frauduleux", "oui", "true", "1"})
_GENUINE_WORDS = frozenset({"vrai", "genuine", "sain", "authentique", "non", "false", "0"})

Line = Literal["trusted_check_failed", "unreadable", "not_flagged", "reviewed"]


class DecisionSetupError(RuntimeError):
    """The decision cannot be made: the checks never ran."""


FAMILIES = ("licence", "releve", "identity", "carte_grise", "rib")


class DecisionConfig(BaseModel):
    trusted: list[str]
    confidence: dict[str, float] = Field(default_factory=dict)
    families: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Optional: a trusted check listed here may flag only documents of these families. A check measured "
        "as reliable on relevés is not thereby reliable on identity cards.",
    )

    def may_flag(self, check: str, family: str | None) -> bool:
        """True when ``check`` is trusted, and trusted for a document of ``family``."""
        if check not in self.trusted:
            return False
        allowed = self.families.get(check)
        return allowed is None or family in allowed


class Decision(BaseModel):
    id: str = Field(description="Organisers' id, as in the spreadsheet.")
    documents: list[str] = Field(default_factory=list, description="Records behind it (DOC-042, DOC-042~2...).")
    flagged: bool
    line: Line
    trusted_failed: list[str] = Field(default_factory=list)
    confidence: float | None = None
    justification: str | None = None
    clues: list[str] = Field(default_factory=list)
    fraud_type: str | None = None
    generated: bool = Field(False, description="A failed trusted check points to a picture made by an AI tool.")
    reported: list[str] = Field(default_factory=list, description="Untrusted checks that failed: informative only.")
    review: bool = False
    review_reasons: list[str] = Field(
        default_factory=list, description="Why a person should look: unreadable, or each untrusted failure with its evidence."
    )
    reviewed: bool = Field(False, description="A person examined the document: their decision replaced the automatic one.")


class ReviewDecision(BaseModel):
    """What a person decided after examining one document."""

    id: str
    fake: bool
    reason: str = ""
    generated: bool = Field(False, description="The person judges the document made by an AI tool.")


@dataclass
class DecisionRun:
    decisions: list[Decision]
    decisions_path: Path
    review_path: Path
    models: list[str]


def load_decision_config(path: Path) -> DecisionConfig:
    """Read ``config/decision.toml``. Raises ``ConfigError`` when it is missing, invalid or names an unknown check."""
    try:
        config = DecisionConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ConfigError(f"decision rules not found: {path}") from exc
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(f"decision rules could not be read: {path} ({exc})") from exc
    known = set(registered())
    unknown = sorted(set(config.trusted) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown trusted check(s) {unknown}; known: {sorted(known)}")
    for name, value in config.confidence.items():
        if not 0 <= value <= 1:
            raise ConfigError(f"{path}: confidence of {name} must be between 0 and 1")
    for name, families in config.families.items():
        if name not in config.trusted:
            raise ConfigError(f"{path}: [families] names {name}, which is not a trusted check")
        if not families or set(families) - set(FAMILIES):
            raise ConfigError(f"{path}: families of {name} must be some of {list(FAMILIES)}")
    return config


def organiser_base(doc_id: str) -> str:
    """The organisers' id behind a record id: DOC-042~2 belongs to DOC-042."""
    return doc_id.split(DUPLICATE_MARK, 1)[0]


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in text if not unicodedata.combining(ch)).casefold().split())


def leaks(text: str, readings: Iterable[Reading | None]) -> bool:
    """True when ``text`` quotes a value the reader copied from the document (a name, a number...)."""
    haystack = _normalise(text)
    for reading in readings:
        if reading is None or reading.answer is None:
            continue
        for item in reading.answer.fields:
            for value in [item.value, *item.other_copies]:
                if value and len(_normalise(value)) >= _MIN_LEAK_LENGTH and _normalise(value) in haystack:
                    return True
    return False


def _unreadable(records: list[DocumentRecord], readings: list[Reading | None]) -> list[str]:
    if not records:
        return ["aucun fichier pour cette ligne du tableur"]
    reasons = []
    for record, reading in zip(records, readings, strict=True):
        if not record.pages:
            code = record.error.code.value if record.error else "aucune page"
            reasons.append(f"{record.id} : fichier illisible ({code})")
        elif reading is not None and not reading.usable:
            why = reading.reason or (f"lisibilité « {reading.answer.readable} »" if reading.answer else "pas de lecture")
            reasons.append(f"{record.id} : lecture impossible ({why})")
    return reasons if len(reasons) == len(records) else []


def decide_one(
    doc_id: str,
    records: list[DocumentRecord],
    results: Results,
    readings: list[Reading | None],
    config: DecisionConfig,
) -> Decision:
    """Decide one document from its own check results and its own reading."""
    failed: list[tuple[str, CheckResult]] = []
    reported: set[str] = set()
    review: list[str] = []
    for record in records:
        for name, result in sorted(results.get(record.id, {}).items()):
            if result.outcome == "fail" and config.may_flag(name, record.family):
                failed.append((name, result))
            elif result.outcome == "fail":
                reported.add(name)
                review.append(f"{name} : {result.evidence}")
    documents = [record.id for record in records]
    if failed:
        # The most trusted failure speaks first: its evidence opens the justification and gives the type.
        failed.sort(key=lambda item: (-config.confidence.get(item[0], 0.5), item[0]))
        evidence = []
        for _, result in failed:
            if result.evidence not in evidence:
                evidence.append(result.evidence)
        justification = " ".join(evidence)
        if leaks(justification, readings):
            log.warning("justification of %s replaced: it quoted a value read on the document", doc_id)
            justification = _GENERIC_JUSTIFICATION
        clues = list(dict.fromkeys(clue for _, result in failed for clue in result.clues))
        names = sorted({name for name, _ in failed})
        return Decision(
            id=doc_id, documents=documents, flagged=True, line="trusted_check_failed", trusted_failed=names,
            confidence=max(config.confidence.get(name, 0.5) for name in names), justification=justification,
            clues=clues, fraud_type=next((result.fraud_type for _, result in failed if result.fraud_type), None),
            generated=any(result.ai_generated for _, result in failed),
            reported=sorted(reported), review=bool(review), review_reasons=review,
        )
    unreadable = _unreadable(records, readings)
    if unreadable:
        return Decision(id=doc_id, documents=documents, flagged=False, line="unreadable", reported=sorted(reported),
                        review=True, review_reasons=unreadable + review)
    return Decision(id=doc_id, documents=documents, flagged=False, line="not_flagged", reported=sorted(reported),
                    review=bool(review), review_reasons=review)


def load_review_decisions(path: Path) -> dict[str, ReviewDecision]:
    """The decisions a person wrote down, by document id. No file means no decision.

    One line per document, separated by ``;`` or ``,``: id, decision (``faux`` or ``vrai``), reason, and
    optionally ``ia`` (``oui`` when the person judges the document made by an AI tool). A header line is
    allowed. A line that cannot be understood stops the run: a decision must never be lost silently.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"review decisions could not be read: {path} ({exc})") from exc
    decisions: dict[str, ReviewDecision] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # One line per document. A reason may hold commas, so ";" separates whenever the line has one.
        cells = [cell.strip() for cell in next(csv.reader([line], delimiter=";" if ";" in line else ","))]
        if not any(cells) or cells[0].casefold() == "id":
            continue
        word = cells[1].casefold() if len(cells) > 1 else ""
        if word not in _FAKE_WORDS | _GENUINE_WORDS:
            raise ConfigError(f"{path}, line {number}: the decision must be 'faux' or 'vrai'")
        fake = word in _FAKE_WORDS
        reason = cells[2] if len(cells) > 2 else ""
        if fake and not reason:
            raise ConfigError(f"{path}, line {number}: a document judged fake needs a reason")
        generated = len(cells) > 3 and cells[3].casefold() in _FAKE_WORDS
        decisions[cells[0]] = ReviewDecision(id=cells[0], fake=fake, reason=reason, generated=generated)
    return decisions


def apply_review(decision: Decision, review: ReviewDecision, readings: Iterable[Reading | None]) -> Decision:
    """A person's decision replaces the automatic one; what the checks found stays attached for the record."""
    if not review.fake:
        return decision.model_copy(update={
            "flagged": False, "line": "reviewed", "reviewed": True, "confidence": None, "justification": None,
            "clues": [], "fraud_type": None, "generated": False,
        })
    reason = review.reason
    if leaks(reason, readings):
        log.warning("review reason of %s replaced: it quoted a value read on the document", decision.id)
        reason = "Document jugé faux par une personne après examen ; motif dans le dossier d'analyse de l'équipe."
    justification = reason if not decision.justification else f"{reason} {decision.justification}"
    return decision.model_copy(update={
        "flagged": True, "line": "reviewed", "reviewed": True,
        "confidence": max(decision.confidence or 0.0, REVIEW_CONFIDENCE),
        "justification": justification,
        "clues": list(dict.fromkeys([_REVIEW_CLUE, *decision.clues])),
        "fraud_type": decision.fraud_type or ("document généré par IA" if review.generated else "document examiné"),
        "generated": decision.generated or review.generated,
    })


def decide(ingest: IngestSettings, settings: DecisionSettings, results: Results | None = None) -> DecisionRun:
    """Decide every document of the spreadsheet (or of the index when there is no spreadsheet) and save it all."""
    config = load_decision_config(settings.decision_file)
    reviews = load_review_decisions(settings.review_decisions_file)
    results = load_results(ingest) if results is None else results
    if not results:
        raise DecisionSetupError("no check results in the cache: run 'python -m fraudcheck check' first")
    try:
        entries = load_index(ingest).documents
    except FileNotFoundError as exc:
        raise DecisionSetupError("no index in the cache: run 'python -m fraudcheck ingest' first") from exc
    groups: dict[str, list[str]] = {}
    for entry in entries:
        groups.setdefault(organiser_base(entry.id), []).append(entry.id)
    catalogue = load_catalogue(ingest.catalogue_file, ingest.catalogue_sheet)
    ids = [row.id for row in catalogue.rows.values()] if catalogue.available else sorted(groups, key=natural_key)
    decisions, models = [], set()
    for doc_id in ids:
        records = [record for record in (load_record(ingest, rid) for rid in groups.get(doc_id, [])) if record]
        readings = [load_reading(ingest, record.id) for record in records]
        models.update(reading.model for reading in readings if reading and reading.status == "read" and reading.model)
        decision = decide_one(doc_id, records, results, readings, config)
        if doc_id in reviews:
            decision = apply_review(decision, reviews[doc_id], readings)
        decisions.append(decision)
    unknown = sorted(set(reviews) - set(ids), key=natural_key)
    if unknown:  # the review file belongs to one set of documents; on another set its lines name nothing
        log.warning("review decisions ignored, no such document in this set: %s", ", ".join(unknown))
    folder = ingest.cache_path / FOLDER
    decisions_path = folder / DECISIONS_NAME
    atomic_write_text(decisions_path, json.dumps([d.model_dump(mode="json") for d in decisions], indent=1, ensure_ascii=False))
    review_path = folder / REVIEW_NAME
    atomic_write_text(review_path, _review_csv(decisions))
    return DecisionRun(decisions, decisions_path, review_path, sorted(models))


def review_order(decisions: list[Decision]) -> list[Decision]:
    """The review list: most failed untrusted checks first, then the unreadable documents, then by id."""
    listed = [decision for decision in decisions if decision.review]
    return sorted(listed, key=lambda d: (-len(d.reported), d.line != "unreadable", natural_key(d.id)))


def _review_csv(decisions: list[Decision]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["id", "flagged", "unreadable", "failed_checks", "checks", "reasons"])
    for decision in review_order(decisions):
        writer.writerow([decision.id, "oui" if decision.flagged else "non", "oui" if decision.line == "unreadable" else "non",
                         len(decision.reported), ", ".join(decision.reported), " | ".join(decision.review_reasons)])
    return "﻿" + buffer.getvalue()


def load_decisions(ingest: IngestSettings) -> list[Decision]:
    try:
        data = json.loads((ingest.cache_path / FOLDER / DECISIONS_NAME).read_text(encoding="utf-8"))
        return [Decision.model_validate(item) for item in data]
    except (OSError, ValueError, ValidationError):
        return []
