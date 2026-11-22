"""Asylum attestation: the days its two counter dates fall on.

asylum_counter_days: an asylum attestation is handed over at a prefecture counter, and the
date of first registration it prints is the day its holder was received at the single desk
for asylum seekers (a prefecture and OFII counter, by appointment). Both are days on which
that counter is open. A date of issue or of first registration that falls on a Sunday or on
a French public holiday fails: no counter produced it, the date was typed again.

A Saturday does not fail. Counters are closed on Saturdays as a rule, but a prefecture may
open one to clear a backlog: the evidence says the date falls on a Saturday, for the person
who validates, and the check passes.

The rule looks at two dates of the document itself: it does not depend on the day of the
check, and it compares no document with another. The date of first registration is a field
of the reading (config/doc_types/attestation_asile.toml). A reading made before that field
existed does not have it: the date of issue alone is judged, and the evidence says so.
"""

from __future__ import annotations

from datetime import date, timedelta

from fraudcheck.checks.base import CheckResult, DocumentContext, register
from fraudcheck.checks.family._values import ReadingCheck, doc_type, parse_date, value

#: The dates a counter produces, with the words the evidence uses for each.
COUNTER_DATES = {
    "date_delivrance": "date de délivrance",
    "date_premier_enregistrement": "date de premier enregistrement en guichet unique",
}
#: Public holidays on a fixed day of the year, (month, day), for the whole of France (code du travail, L. 3133-1).
FIXED_HOLIDAYS = frozenset({(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)})
#: Public holidays counted in days from Easter Sunday: Easter Monday, Ascension Thursday, Whit Monday.
EASTER_OFFSETS = (1, 39, 50)
SATURDAY, SUNDAY = 5, 6


def easter_sunday(year: int) -> date:
    """Easter Sunday of a year of the Gregorian calendar (Meeus's algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def public_holiday(day: date) -> bool:
    """True on one of the eleven public holidays of the whole of France."""
    if (day.month, day.day) in FIXED_HOLIDAYS:
        return True
    easter = easter_sunday(day.year)
    return any(day == easter + timedelta(days=offset) for offset in EASTER_OFFSETS)


def closed_day(day: date) -> str | None:
    """Why no counter is open on that day ("un dimanche", "un jour férié"); ``None`` on any other day."""
    if day.weekday() == SUNDAY:
        return "un dimanche"
    if public_holiday(day):
        return "un jour férié"
    return None


@register
class AsylumCounterDays(ReadingCheck):
    name = "asylum_counter_days"
    source = "general"
    description = ("Asylum attestation: its date of issue and its date of first registration at the single desk "
                   "fall on neither a Sunday nor a public holiday.")
    families = frozenset({"identity"})

    def applies(self, context: DocumentContext) -> bool:
        kind = doc_type(context)
        return (super().applies(context) and kind is not None and kind.name == "attestation_asile"
                and any(value(context, name)[1] == "read" for name in COUNTER_DATES))

    def run(self, context: DocumentContext) -> CheckResult:
        today = context.settings.today()
        judged: list[str] = []
        closed: dict[str, str] = {}
        saturdays: list[str] = []
        unreadable: list[str] = []
        for name in COUNTER_DATES:
            text, state = value(context, name)
            if state != "read":
                continue
            day = parse_date(text, today.year).value
            if day is None:
                unreadable.append(name)
                continue
            judged.append(name)
            reason = closed_day(day)
            if reason:
                closed[name] = reason
            elif day.weekday() == SATURDAY:
                saturdays.append(name)
        details = {"judged": judged, "closed": closed, "saturdays": saturdays,
                   "not_read": [name for name in COUNTER_DATES if name not in judged]}
        if closed:
            said = " et ".join(f"la {COUNTER_DATES[name]} tombe {reason}" for name, reason in closed.items())
            return self.result(
                "fail",
                f"{said[0].upper()}{said[1:]} : le guichet de la préfecture ne reçoit et ne délivre ni le dimanche ni "
                "un jour férié.",
                fields=list(closed),
                clues=[f"{COUNTER_DATES[name]} {reason}" for name, reason in closed.items()],
                fraud_type="date modifiée",
                details=details,
            )
        if not judged:
            return self.result("cannot_tell", "Date de délivrance ou de premier enregistrement illisible.",
                               fields=unreadable, details=details)
        evidence = f"{len(judged)} date(s) de guichet lue(s) : aucune ne tombe un dimanche ni un jour férié."
        if saturdays:
            evidence += (f" La {' et la '.join(COUNTER_DATES[name] for name in saturdays)} tombe un samedi, jour où "
                         "peu de préfectures reçoivent : à regarder.")
        if "date_premier_enregistrement" not in judged:
            evidence += " La date de premier enregistrement n'a pas été lue : seule la date de délivrance est jugée."
        return self.result("pass", evidence, fields=judged, details=details)
