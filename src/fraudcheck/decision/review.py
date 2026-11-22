"""A person's decisions, written one line per document in the review file.

The reviewer screen (``fraudcheck/reviewer_app.py``) writes them here; ``decide`` reads
the same file (``load_review_decisions``) and lets them replace the automatic decision.
Header and comment lines of the file are kept as they are.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from fraudcheck.decision.decide import ReviewDecision
from fraudcheck.ingest.storage import atomic_write_text

HEADER = "id;decision;raison;ia"
_COMMENTS = (
    "# Décisions prises par une personne après examen d'un document. Elles remplacent la décision automatique.",
    "# Colonnes : identifiant ; faux ou vrai ; motif (sans aucune valeur recopiée du document) ; oui si généré par IA.",
)


def _line(decision: ReviewDecision) -> str:
    buffer = io.StringIO()
    reason = " ".join(decision.reason.split())  # one line per document: line breaks become spaces
    csv.writer(buffer, delimiter=";", lineterminator="").writerow(
        [decision.id, "faux" if decision.fake else "vrai", reason, "oui" if decision.generated else "non"]
    )
    return buffer.getvalue()


def _id_of(line: str) -> str | None:
    """The document a line of the file is about; ``None`` for a blank, a comment or the header."""
    first = line.replace(",", ";").split(";", 1)[0].strip()
    return None if not first or first.startswith("#") or first.casefold() == "id" else first


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return [HEADER, *_COMMENTS]


def save_review_decision(path: Path, decision: ReviewDecision) -> None:
    """Write or replace the line of one document. A document judged fake needs its reason."""
    if decision.fake and not decision.reason.strip():
        raise ValueError("a document judged fake needs a reason")
    lines, written = _lines(path), False
    for number, line in enumerate(lines):
        if _id_of(line) == decision.id:
            lines[number], written = _line(decision), True
    if not written:
        lines.append(_line(decision))
    atomic_write_text(path, "\n".join(lines) + "\n")


def remove_review_decision(path: Path, doc_id: str) -> bool:
    """Take a document's line out of the file, so the automatic decision applies again. True when there was one."""
    lines = _lines(path)
    kept = [line for line in lines if _id_of(line) != doc_id]
    if len(kept) == len(lines):
        return False
    atomic_write_text(path, "\n".join(kept) + "\n")
    return True
