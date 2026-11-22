"""Integration test on the organisers' documents, when they are present in data/.

It checks counts and document ids only, and never prints document content. The
outputs go to a temporary folder that is deleted at the end, so the team's cache is
left alone. It takes about a minute (ingest, then the laptop checks and the decision).
Expected: the four fallback-font PDFs, the six pictures at an image generator's pixel budget, the
PDFs whose text lies under a box painted after it, and the four scans with text, shapes or notes
laid over them. The team's own review decisions are left out: only the automatic checks are tested.
"""

from __future__ import annotations

import shutil
from collections import Counter

import pytest

from fraudcheck.checks.runner import run_checks
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.decision.decide import decide
from fraudcheck.decision.settings import DecisionSettings
from fraudcheck.ingest import IngestSettings, run_ingest

DEFAULTS = IngestSettings()
pytestmark = [
    pytest.mark.real_data,
    pytest.mark.skipif(
        not (DEFAULTS.documents_path.is_dir() and DEFAULTS.catalogue_file.is_file()),
        reason="the organisers' documents or spreadsheet are not in data/",
    ),
]
FALLBACK_FONT_PDFS = {"DOC-433", "DOC-684", "DOC-712", "DOC-922"}
GENERATOR_BUDGET = {"DOC-140", "DOC-291", "DOC-707", "DOC-716", "DOC-849", "DOC-987"}  # 1024 x 1536 pixels, free shape
COVERED_TEXT = FALLBACK_FONT_PDFS | {"DOC-542", "DOC-591"}  # text under an opaque box painted after it
SCAN_OVERLAY = {"DOC-095", "DOC-331", "DOC-409", "DOC-722"}  # text, shapes or notes laid over a scanned page
LAYERED_SCANS = {"DOC-061", "DOC-069", "DOC-095", "DOC-120", "DOC-694", "DOC-884", "DOC-890"}  # office-scanner layers


def test_real_set_counts_and_laptop_checks(tmp_path) -> None:
    # The local text reader is left out here: it takes ten minutes on the set, and its check has its own tests.
    settings = DEFAULTS.model_copy(update={"cache_dir": tmp_path / "cache", "local_words": False})
    try:
        first = run_ingest(settings)
        records = {record.id: record for record in first.records}
        assert len(records) == 137
        assert sum(len(record.pages) for record in records.values()) == 206
        assert Counter(record.kind for record in records.values()) == {"picture": 70, "pdf_scan": 41, "pdf_text": 26}
        assert Counter(record.family for record in records.values()) == {
            "licence": 51, "releve": 36, "identity": 21, "carte_grise": 18, "rib": 11
        }
        assert [record.id for record in records.values() if record.error] == []
        assert "LiberationSans" in records["DOC-433"].fonts
        assert len(first.index.documents) == 137 and not first.index.catalogue_rows_without_file
        assert run_ingest(settings).reused == set(records)

        checks = run_checks(settings, CheckSettings())
        def failing(check: str) -> set[str]:
            return {doc for doc, answers in checks.results.items() if answers.get(check) and answers[check].outcome == "fail"}

        assert failing("font") == FALLBACK_FONT_PDFS
        assert failing("generated_size") == GENERATOR_BUDGET
        assert failing("covered_text") == COVERED_TEXT
        assert failing("scan_overlay") == SCAN_OVERLAY
        assert {doc for doc, record in records.items() if any(page.scanner_layers for page in record.pages)} == LAYERED_SCANS
        assert sum(1 for record in records.values() for page in record.pages if page.pixel_size) == 132

        decision = decide(settings, DecisionSettings(review_decisions_path=tmp_path / "no-review-decisions.csv"))
        automatic = FALLBACK_FONT_PDFS | GENERATOR_BUDGET | COVERED_TEXT | SCAN_OVERLAY
        assert {d.id for d in decision.decisions if d.flagged} == automatic
        assert {d.id for d in decision.decisions if d.generated} == GENERATOR_BUDGET
        assert not any(d.reviewed for d in decision.decisions)
    finally:
        shutil.rmtree(tmp_path / "cache", ignore_errors=True)
