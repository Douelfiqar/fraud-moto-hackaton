"""The days an asylum attestation's counter dates fall on. Hand-made readings only: every date is invented."""

from __future__ import annotations

from datetime import date

import pytest

from fraudcheck.checks.base import registered
from fraudcheck.checks.family.asylum_days import closed_day, easter_sunday, public_holiday
from test_checks_family import context, field, outcome

CHECK = "asylum_counter_days"


def reading(issued: str | None, registered_on: str | None):
    return context("attestation_asile", [field("date_delivrance", issued),
                                         field("date_premier_enregistrement", registered_on)])


def test_easter_and_the_public_holidays_counted_from_it() -> None:
    assert easter_sunday(2024) == date(2024, 3, 31) and easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert public_holiday(date(2026, 4, 6)) and public_holiday(date(2026, 5, 14)) and public_holiday(date(2026, 5, 25))
    assert public_holiday(date(2026, 7, 14)) and not public_holiday(date(2026, 5, 26))
    assert closed_day(date(2026, 3, 1)) == "un dimanche" and closed_day(date(2026, 7, 14)) == "un jour férié"
    assert closed_day(date(2026, 3, 2)) is None and closed_day(date(2026, 3, 7)) is None  # a Monday, a Saturday


@pytest.mark.parametrize(
    ("issued", "registered_on", "expected"),
    [("02/03/2026", "05/05/2025", "pass"),  # two Mondays
     ("01/03/2026", "05/05/2025", "fail"),  # issued on a Sunday
     ("02/03/2026", "04/05/2025", "fail"),  # registered at the single desk on a Sunday
     ("14/07/2026", "05/05/2025", "fail"),  # issued on 14 July, a Tuesday
     ("06/04/2026", "05/05/2025", "fail"),  # issued on Easter Monday
     ("02/03/2026", "29/05/2025", "fail"),  # registered on Ascension Thursday
     ("07/03/2026", "05/05/2025", "pass"),  # a Saturday is said in the evidence and does not fail
     ("02/03/2026", None, "pass"),  # a reading made before the field existed: the date of issue alone
     (None, "04/05/2025", "fail"),  # the date of first registration alone is enough to fail
     ("date effacée", None, "cannot_tell"),
     (None, None, None)],
)
def test_counter_dates_fall_on_neither_a_sunday_nor_a_public_holiday(issued, registered_on, expected) -> None:
    assert outcome(CHECK, reading(issued, registered_on)) == expected


def test_the_evidence_names_the_date_and_the_day_without_quoting_the_date() -> None:
    check = registered()[CHECK]
    failed = check.run(reading("02/03/2026", "04/05/2025"))
    assert failed.fields == ["date_premier_enregistrement"] and failed.fraud_type == "date modifiée"
    assert "premier enregistrement" in failed.evidence and "dimanche" in failed.evidence
    assert failed.clues == ["date de premier enregistrement en guichet unique un dimanche"]
    both = check.run(reading("01/03/2026", "29/05/2025"))
    assert both.fields == ["date_delivrance", "date_premier_enregistrement"] and len(both.clues) == 2
    saturday = check.run(reading("07/03/2026", "05/05/2025"))
    assert saturday.outcome == "pass" and "samedi" in saturday.evidence
    alone = check.run(reading("02/03/2026", None))
    assert "n'a pas été lue" in alone.evidence and alone.details["not_read"] == ["date_premier_enregistrement"]


def test_other_documents_are_not_judged() -> None:
    fields = [field("date_delivrance", "01/03/2026")]
    assert outcome(CHECK, context("attestation_prolongation", fields)) is None  # issued online, any day
    assert outcome(CHECK, context("titre_sejour_recto", fields)) is None
