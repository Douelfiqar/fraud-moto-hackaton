"""Evaluation on marked examples: fakes caught, wrong flags, the score, and each check's hits.

The marked examples are a CSV file, ``data/marked_examples.csv`` by default, with a
document id and a label (``fake`` or ``genuine``; ``faux``/``vrai`` also work),
separated by commas or semicolons. A missing or empty file is fine: the
evaluation then only counts flags.

Score, as the organisers compute it: +1 per fake caught, and past 3% of wrong
flags among the evaluated documents, -2 points per extra percentage point.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fraudcheck.checks.runner import Results
from fraudcheck.decision.decide import Decision, organiser_base
from fraudcheck.ingest.discover import natural_key, organiser_id

FREE_SHARE = 0.03
PENALTY_PER_POINT = 2.0
_FAKE = {"fake", "faux", "fraud", "fraude", "frauduleux", "true", "1", "oui", "yes"}
_GENUINE = {"genuine", "vrai", "sain", "authentique", "false", "0", "non", "no"}
_ID_HEADERS = {"id", "doc_id", "document", "doc"}
_LABEL_HEADERS = {"label", "etiquette", "étiquette", "verdict", "statut", "frauduleux"}


@dataclass
class MarkedExamples:
    labels: dict[str, bool] = field(default_factory=dict)  # id to True when fake
    problems: list[str] = field(default_factory=list)


@dataclass
class CheckHits:
    name: str
    fails_on_fakes: int
    fails_on_genuine: int
    fails_overall: int


@dataclass
class Evaluation:
    flagged: list[str]
    marked: int
    fakes: int
    caught: list[str]
    wrong: list[str]
    missed: list[str]
    evaluated: int
    false_positive_rate: float
    penalty: float
    score: float
    free_wrong_flags: int
    checks: list[CheckHits]
    problems: list[str]


def load_marked(path: Path) -> MarkedExamples:
    """Read the marked examples; any problem is reported, never raised."""
    marked = MarkedExamples()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        marked.problems.append(f"no marked examples file at {path}")
        return marked
    except (OSError, UnicodeDecodeError) as exc:
        marked.problems.append(f"marked examples could not be read ({type(exc).__name__})")
        return marked
    rows = [row for row in csv.reader(text.splitlines(), delimiter=";" if text.count(";") > text.count(",") else ",") if row]
    if not rows:
        marked.problems.append("the marked examples file is empty")
        return marked
    header = [cell.strip().lower() for cell in rows[0]]
    id_col = next((i for i, cell in enumerate(header) if cell in _ID_HEADERS), None)
    label_col = next((i for i, cell in enumerate(header) if cell in _LABEL_HEADERS), None)
    if id_col is None or label_col is None:
        id_col, label_col, body = 0, 1, rows  # no header: first column id, second label
    else:
        body = rows[1:]
    for number, row in enumerate(body, start=2 if body is not rows else 1):
        if len(row) <= max(id_col, label_col):
            marked.problems.append(f"line {number}: too few columns")
            continue
        doc_id = organiser_id(row[id_col]) or row[id_col].strip()
        label = row[label_col].strip().lower()
        if label in _FAKE:
            marked.labels[doc_id] = True
        elif label in _GENUINE:
            marked.labels[doc_id] = False
        else:
            marked.problems.append(f"line {number}: unknown label (use fake or genuine)")
    return marked


def evaluate(decisions: Sequence[Decision], results: Results, marked: MarkedExamples, evaluated: int) -> Evaluation:
    flagged = sorted((d.id for d in decisions if d.flagged), key=natural_key)
    labels = marked.labels
    fakes = {doc_id for doc_id, fake in labels.items() if fake}
    genuine = set(labels) - fakes
    caught = sorted(set(flagged) & fakes, key=natural_key)
    wrong = sorted(set(flagged) & genuine, key=natural_key)
    missed = sorted(fakes - set(flagged), key=natural_key)
    rate = len(wrong) / evaluated
    penalty = PENALTY_PER_POINT * max(0.0, 100 * rate - 100 * FREE_SHARE)
    names = sorted({name for answers in results.values() for name in answers})
    hits = []
    for name in names:
        failing = {organiser_base(doc_id) for doc_id, answers in results.items()
                   if name in answers and answers[name].outcome == "fail"}
        hits.append(CheckHits(name, len(failing & fakes), len(failing & genuine), len(failing)))
    return Evaluation(
        flagged=flagged, marked=len(labels), fakes=len(fakes), caught=caught, wrong=wrong, missed=missed,
        evaluated=evaluated, false_positive_rate=rate, penalty=penalty, score=len(caught) - penalty,
        free_wrong_flags=math.floor(FREE_SHARE * evaluated + 1e-9), checks=hits, problems=list(marked.problems),
    )


def format_evaluation(report: Evaluation) -> str:
    """The evaluation in a few lines of counts and ids."""
    lines = [f"Flagged: {len(report.flagged)} documents" + (f" ({', '.join(report.flagged)})" if report.flagged else "")]
    if not report.marked:
        lines.append("No marked examples: fakes caught and wrong flags cannot be measured.")
    else:
        lines += [
            f"Marked examples: {report.marked} ({report.fakes} fakes, {report.marked - report.fakes} genuine)",
            f"  fakes caught   {len(report.caught)}" + (f" ({', '.join(report.caught)})" if report.caught else ""),
            f"  wrong flags    {len(report.wrong)}" + (f" ({', '.join(report.wrong)})" if report.wrong else ""),
            f"  fakes missed   {len(report.missed)}",
        ]
    lines += [
        f"Score for {report.evaluated} evaluated documents: {report.score:.1f} "
        f"(false-positive rate {report.false_positive_rate:.1%}, {report.free_wrong_flags} wrong flags free, "
        f"penalty {report.penalty:.1f}; each further wrong flag costs {PENALTY_PER_POINT * 100 / report.evaluated:.1f})",
        "Checks (fails on marked fakes / on marked genuine / on all documents):",
    ]
    lines += [f"  {hit.name:<24} {hit.fails_on_fakes} / {hit.fails_on_genuine} / {hit.fails_overall}" for hit in report.checks]
    lines += [f"  note: {problem}" for problem in report.problems]
    return "\n".join(lines)
