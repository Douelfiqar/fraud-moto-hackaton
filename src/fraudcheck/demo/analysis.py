"""The pipeline's stages, run in order on one document's folder, each one reported as it starts and ends.

The stages are the pipeline's own functions (``run_ingest``, ``run_read``,
``run_inspect``, ``run_checks``, ``decide``): nothing is judged here. This module only
chooses where the answers of the two Azure stages come from:

- a saved answer: found again by the request's key in the folders it is given (the
  team's own saved answers) or, for a file of the team's own set, the result the batch
  run saved for that very file (same fingerprint): no network, no cost;
- a live call to the team's Azure deployment, only when the desk was started with it;
- neither: the stage is skipped and says why, and the checks that need it stay silent.

What goes to the page is built here too (``result_payload``): the decision as the
pipeline wrote it and every check's answer. A check's ``details`` never leave: they may
hold values copied from the document.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fraudcheck.checks.base import CheckResult, registered
from fraudcheck.checks.runner import Results, run_checks
from fraudcheck.decision.decide import Decision, DecisionConfig, decide, load_decision_config
from fraudcheck.demo.workspace import StageSettings
from fraudcheck.ingest.models import DocumentRecord
from fraudcheck.ingest.pipeline import run_ingest
from fraudcheck.inspect.inspector import run_inspect
from fraudcheck.inspect.models import Inspection
from fraudcheck.inspect.store import load_inspection, save_inspection
from fraudcheck.read.gateway import (
    AnswerCache,
    AzureGateway,
    Gateway,
    GatewaySetupError,
    ReplayGateway,
    TokenLog,
    missing_configuration,
)
from fraudcheck.read.models import Reading, ReadingAnswer
from fraudcheck.read.reader import run_read
from fraudcheck.read.settings import ReadSettings
from fraudcheck.read.store import load_reading, save_reading

log = logging.getLogger(__name__)

STAGES = ("prepare", "read", "inspect", "check", "decide")
#: A live call from the desk waits less and insists less than a batch run: someone is watching the screen.
LIVE_TIMEOUT_S = 60.0
LIVE_RETRIES = 1


class AnalysisError(RuntimeError):
    """A stage could not run at all (a missing configuration file, a folder that cannot be written)."""


@dataclass
class StageReport:
    """Where one stage stands, with a few counts for the page. Counts only: never a value of the document."""

    key: str
    status: str = "pending"  # pending, running, done or skipped
    seconds: float | None = None
    #: Azure stages: "saved" (a saved answer found by its key), "saved_result" (the batch run's saved result of
    #: this very file) or "live" (a call made now).
    source: str | None = None
    note: str | None = None  # why the stage was skipped, as a short code the page turns into a sentence
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "status": self.status, "seconds": self.seconds, "source": self.source,
                "note": self.note, "facts": dict(self.facts)}


@dataclass
class Analysed:
    """What the stages produced for one document."""

    record: DocumentRecord
    answers: dict[str, CheckResult]
    decision: Decision
    skipped: list[str]  # the Azure stages that did not run


class SavedAnswers(AnswerCache):
    """Saved Azure answers, looked up in several folders.

    The folders given as ``read_only`` (the team's own saved answers) are only ever read.
    An answer paid for during the session is kept in the session's own folder.
    """

    def __init__(self, own: Path, read_only: Sequence[Path] = ()) -> None:
        super().__init__(own)
        self._read_only = [AnswerCache(folder) for folder in read_only]

    def get(self, key: str, schema: type = ReadingAnswer):
        for cache in self._read_only:
            if (answer := cache.get(key, schema)) is not None:
                return answer
        return super().get(key, schema)


class Analyst:
    """Runs the stages on one document's folder."""

    def __init__(
        self,
        settings: StageSettings,
        answers: SavedAnswers,
        *,
        live: bool = False,
        preferred_models: Mapping[str, str | None] | None = None,
        saved_results: Mapping[str, Path] | None = None,
        report: Callable[[StageReport], None] | None = None,
        live_gateway: Callable[[ReadSettings], Gateway] | None = None,
    ) -> None:
        self.settings, self.answers, self.live = settings, answers, live
        self.preferred_models = dict(preferred_models or {})
        self.saved_results = dict(saved_results or {})  # stage -> the batch run's saved result of this very file
        self._report = report or (lambda stage: None)
        self._live_gateway = live_gateway or self._azure_gateway
        self._record: DocumentRecord | None = None
        self._results: Results = {}
        self._decision: Decision | None = None
        self._skipped: list[str] = []

    def run(self) -> Analysed:
        """Every stage in the pipeline's order. Raises ``AnalysisError`` when a stage cannot run at all."""
        try:
            for key, work in (("prepare", self._prepare), ("read", self._read), ("inspect", self._inspect),
                              ("check", self._check), ("decide", self._decide)):
                self._stage(key, work)
        except AnalysisError:
            raise
        except RuntimeError as exc:  # the pipeline's own setup errors: configuration, cache, missing stage
            raise AnalysisError(f"{type(exc).__name__}: {exc}") from exc
        assert self._record is not None and self._decision is not None
        return Analysed(self._record, self._results.get(self._record.id, {}), self._decision, self._skipped)

    def _stage(self, key: str, work: Callable[[StageReport], None]) -> None:
        report = StageReport(key, status="running")
        self._report(report)
        started = time.perf_counter()
        work(report)
        report.seconds = round(time.perf_counter() - started, 2)
        if report.status == "running":
            report.status = "done"
        self._report(report)

    # Step 1: one picture per page, and what the two local readers find on them.

    def _prepare(self, report: StageReport) -> None:
        run = run_ingest(self.settings.ingest)
        if not run.records:
            raise AnalysisError("the document's folder holds no file")
        record = self._record = run.records[0]
        codes = [page.barcodes for page in record.pages if page.barcodes is not None]
        words = [page.printed_words for page in record.pages if page.printed_words is not None]
        report.facts = {
            "pages": len(record.pages),
            "kind": record.kind,
            "codes": sum(map(len, codes)) if codes else None,  # None: the reader did not run
            "words": sum(map(len, words)) if words else None,
            "error": record.error.code.value if record.error else None,
        }

    # Steps 2 and 3: the two Azure answers, saved or live.

    def _read(self, report: StageReport) -> None:
        ingest = self.settings.ingest
        answered = self._azure_stage(
            report, "read",
            lambda connection, gateway, replay: run_read(ingest, connection, replay=replay, gateway=gateway),
            lambda run: bool(run.readings),
        )
        reading = load_reading(ingest, self._record.id) if answered else None
        if reading is None:
            return
        fields = reading.answer.fields if reading.answer else []
        report.facts = {
            "read": reading.status == "read",
            "reason": reading.reason if reading.status != "read" else None,
            "fields": sum(1 for item in fields if item.state == "read"),
            "readable": reading.answer.readable if reading.answer else None,
            "turned": len(reading.turns),
            "turns": {str(page): degrees for page, degrees in reading.turns.items()},  # sideways pages, for the viewer
            "model": reading.model,
        }

    def _inspect(self, report: StageReport) -> None:
        ingest, settings = self.settings.ingest, self.settings.inspection
        answered = self._azure_stage(
            report, "inspect",
            lambda connection, gateway, replay: run_inspect(ingest, settings, connection, replay=replay, gateway=gateway),
            lambda run: bool(run.inspections),
            max_completion_tokens=settings.max_completion_tokens,
        )
        inspection = load_inspection(ingest, self._record.id) if answered else None
        if inspection is None:
            return
        answer = inspection.answer
        report.facts = {
            "inspected": inspection.status == "inspected",
            "reason": inspection.reason if inspection.status != "inspected" else None,
            "zones": len(answer.zones) if answer else 0,
            "observations": len(answer.typography) if answer else 0,
            "tiles": inspection.tiles,
            "references": inspection.references,
            "model": inspection.model,
        }

    def _azure_stage(self, report: StageReport, key: str, run_stage: Callable, answered: Callable[[Any], bool],
                     max_completion_tokens: int | None = None) -> bool:
        """Run one Azure stage from a saved answer when there is one, else live when allowed, else not at all.

        True when the stage left its result in the document's folder.
        """
        assert self._record is not None
        if not self._record.pages:
            return self._skip(report, key, "no_page")
        replay = ReplayGateway(self.answers)
        for model in self._models(key):
            if answered(run_stage(self._connection(model), replay, True)):
                report.source = "saved"
                return True
        if self._copy_saved_result(key):
            report.source = "saved_result"
            return True
        if not self.live:
            return self._skip(report, key, "no_saved_answer")
        connection = self.settings.reading.model_copy(update={
            "max_retries": min(self.settings.reading.max_retries, LIVE_RETRIES),
            "timeout_seconds": min(self.settings.reading.timeout_seconds, LIVE_TIMEOUT_S),
            **({"max_completion_tokens": max_completion_tokens} if max_completion_tokens else {}),
        })
        if missing_configuration(connection):
            return self._skip(report, key, "azure_not_configured")
        try:
            run = run_stage(connection, self._live_gateway(connection), False)
        except GatewaySetupError as exc:
            log.warning("Azure could not be used for the %s stage: %s", key, exc)
            return self._skip(report, key, "azure_unreachable")
        if not answered(run):
            return self._skip(report, key, "azure_no_answer")
        report.source = "live" if run.live_calls else "saved"
        return True

    def _copy_saved_result(self, key: str) -> bool:
        """Take the result the batch run saved for this very file, when the request's key no longer finds its
        answer (a prompt or a field list edited since). Only a result with the same fingerprint is taken."""
        path = self.saved_results.get(key)
        if path is None or self._record is None:
            return False
        shape, save = (Reading, save_reading) if key == "read" else (Inspection, save_inspection)
        try:
            saved = shape.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            return False
        if saved.fingerprint != self._record.fingerprint:
            return False
        save(self.settings.ingest, saved.model_copy(update={"id": self._record.id}))
        return True

    def _skip(self, report: StageReport, key: str, note: str) -> bool:
        report.status, report.note = "skipped", note
        self._skipped.append(key)
        return False

    def _models(self, key: str) -> list[str | None]:
        """Deployments whose saved answers are looked for: the one the saved result of this very file names,
        then the default one, then the stronger one. ``None`` stands for "no deployment configured"."""
        reading = self.settings.reading
        models = [model for model in (self.preferred_models.get(key), reading.deployment, reading.model, reading.strong_model)
                  if model]
        return list(dict.fromkeys(models)) or [None]

    def _connection(self, model: str | None) -> ReadSettings:
        if model is None:
            return self.settings.reading
        return self.settings.reading.model_copy(update={"model": model, "use_strong": False})

    def _azure_gateway(self, connection: ReadSettings) -> Gateway:
        return AzureGateway(connection, self.answers, TokenLog(self.answers.folder.parent / "tokens.jsonl"))

    # Steps 4 and 5: every check on this one document, then the decision.

    def _check(self, report: StageReport) -> None:
        assert self._record is not None
        run = run_checks(self.settings.ingest, self.settings.checks)
        self._results = run.results
        answers = run.results.get(self._record.id, {})
        outcomes = Counter(answer.outcome or "score" for answer in answers.values())
        report.facts = {"total": len(run.checks), "applied": len(answers), "pass": outcomes["pass"],
                        "fail": outcomes["fail"], "cannot_tell": outcomes["cannot_tell"]}

    def _decide(self, report: StageReport) -> None:
        run = decide(self.settings.ingest, self.settings.decision, self._results)
        if not run.decisions:
            raise AnalysisError("the decision stage returned nothing for the document")
        decision = self._decision = run.decisions[0]
        report.facts = {"flagged": decision.flagged, "line": decision.line,
                        "trusted_failed": len(decision.trusted_failed), "reported": len(decision.reported)}


def decision_config(settings: StageSettings) -> DecisionConfig:
    return load_decision_config(settings.decision.decision_file)


def decision_payload(decision: Decision) -> dict[str, Any]:
    """A decision as the page shows it: the pipeline's own words, untouched."""
    return {
        "flagged": decision.flagged,
        "line": decision.line,
        "confidence": decision.confidence,
        "fraud_type": decision.fraud_type,
        "generated": decision.generated,
        "justification": decision.justification,
        "clues": list(decision.clues),
        "review_reasons": list(decision.review_reasons),
        "reviewed": decision.reviewed,
    }


def check_rows(answers: Mapping[str, CheckResult], config: DecisionConfig, family: str | None,
               kinds: Mapping[str, str]) -> list[dict[str, Any]]:
    """One row per registered check: its answer for this document, or ``not_applicable``.

    ``trusted`` says whether a failure of this check may flag a document of this family
    (``config/decision.toml``). ``details`` are left out on purpose.
    """
    rows = []
    for name in registered():
        result = answers.get(name)
        rows.append({
            "name": name,
            "kind": kinds.get(name, "values"),
            "outcome": "not_applicable" if result is None else (result.outcome or "score"),
            "evidence": result.evidence if result else None,
            "trusted": config.may_flag(name, family),
        })
    return rows


def result_payload(analysed: Analysed, config: DecisionConfig, kinds: Mapping[str, str]) -> dict[str, Any]:
    """The tool's result for one document: its decision, every check's answer, and what did not run."""
    rows = check_rows(analysed.answers, config, analysed.record.family, kinds)
    counts = Counter(row["outcome"] for row in rows)
    return {
        "decision": decision_payload(analysed.decision),
        "checks": rows,
        "counts": {name: counts.get(name, 0) for name in ("fail", "cannot_tell", "pass", "score", "not_applicable")},
        "skipped": list(analysed.skipped),
    }
