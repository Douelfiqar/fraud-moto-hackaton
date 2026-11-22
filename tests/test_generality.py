"""Guards against fitting the solution to one set of documents.

A rule decides from the content of the document it judges. So nothing in the code, the settings or the
prompts may name a document, and the reference lists the rules use hold facts about the world (makers,
countries), never facts about the documents at hand.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata

import pytest
from conftest import REPO_ROOT

from fraudcheck.checks.base import load_check_modules, registered

#: The id of the organisers' own example: the only one comments may use to show what an id looks like.
EXAMPLE_ID = "DOC-042"
_DOCUMENT_ID = re.compile(r"DOC-\d{3}")
_SEARCHED = (("src", "*.py"), ("config", "*.toml"), ("prompts", "*.md"))


def test_no_document_is_named_in_the_code_the_settings_or_the_prompts() -> None:
    named = {}
    for folder, pattern in _SEARCHED:
        for path in sorted((REPO_ROOT / folder).rglob(pattern)):
            found = set(_DOCUMENT_ID.findall(path.read_text(encoding="utf-8"))) - {EXAMPLE_ID}
            if found:
                named[path.relative_to(REPO_ROOT).as_posix()] = sorted(found)
    assert not named, f"documents named outside the data folder: {named}"


def test_every_trusted_check_exists_and_says_where_its_rule_comes_from() -> None:
    """Trust is given by name in config/decision.toml: a name that matches no check would silently flag nothing,
    and a check that may flag a document must say whether its rule comes from a practice sheet or from general
    knowledge (law, standard, structure of the file)."""
    load_check_modules()
    checks = registered()
    decision = tomllib.loads((REPO_ROOT / "config" / "decision.toml").read_text(encoding="utf-8"))
    trusted = decision["trusted"]
    assert len(trusted) == len(set(trusted))
    assert not [name for name in trusted if name not in checks]
    assert not [name for name in trusted if checks[name].source not in ("fiche", "general")]
    assert not [name for name in trusted if not (checks[name].description or "").strip()]
    assert not [name for name in decision["confidence"] if name not in trusted]
    assert not [name for name in trusted if name not in decision["confidence"]]


def test_the_reference_lists_hold_one_entry_per_fact() -> None:
    """Makers and countries are listed once, with what the rules need: a maker listed under two kinds, or a
    country without a length, would make a rule answer by accident."""
    makers = tomllib.loads((REPO_ROOT / "config" / "vehicle_makers.toml").read_text(encoding="utf-8"))
    for one, two in (("two_wheelers", "cars"), ("two_wheeler_makes", "car_makes")):
        assert not set(makers[one]) & set(makers[two])
        assert len(makers[one]) == len(set(makers[one])) and len(makers[two]) == len(set(makers[two]))
    assert all(re.fullmatch(r"[A-HJ-NPR-Z0-9]{3}", code) for code in [*makers["two_wheelers"], *makers["cars"]])
    numbers = tomllib.loads((REPO_ROOT / "config" / "licence_numbers.toml").read_text(encoding="utf-8"))
    for country in numbers["country"]:
        assert country["name"] and country["words"] and country["length"] > 0 and country["source"]
        assert all(word == word.casefold() and word.isascii() for word in country["words"])


# --- nothing printed on a real document is used as an example ----------------------------------------------------------
# These two guards need the team's own readings of the real documents (cache/, never in Git): they run on the team's
# machines and are skipped elsewhere. They print locations only, never a value.

READINGS = sorted((REPO_ROOT / "cache").glob("DOC-*/reading.json"))
_DATE = re.compile(r"(?<!\d)(\d{1,2})[./ -](\d{1,2})[./ -]((?:19|20)\d{2})(?!\d)")


def _bare(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]", "", "".join(char for char in text if not unicodedata.combining(char)).casefold())


def _dates(text: str) -> set[str]:
    return {f"{int(day):02d}{int(month):02d}{year}" for day, month, year in _DATE.findall(text)}


def _repository_files() -> dict[str, str]:
    files = [REPO_ROOT / "README.md"]
    for folder, pattern in (("src", "*.py"), ("config", "*.toml"), ("prompts", "*.md"), ("tests", "*.py"), ("docs", "*.md")):
        files += sorted((REPO_ROOT / folder).rglob(pattern))
    return {path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8") for path in files if path.is_file()}


def _values_read() -> dict[str, list[str]]:
    """For each real document, every value the reader copied from it (and every other copy of it)."""
    values = {}
    for path in READINGS:
        answer = json.loads(path.read_text(encoding="utf-8")).get("answer") or {}
        values[path.parent.name] = [str(raw) for item in answer.get("fields", [])
                                    for raw in [item.get("value"), *(item.get("other_copies") or [])] if raw]
    return values


@pytest.mark.real_data
@pytest.mark.skipif(not READINGS, reason="no reading of the real documents in cache/")
def test_no_line_of_the_repository_holds_two_dates_of_one_real_document() -> None:
    """One date can be met by chance; two dates of the same document on one line were copied from it. Examples in
    comments and tests are made up: a rule is written from a law or a principle, never from a document's values."""
    dates = {doc: set().union(*(_dates(value) for value in values)) for doc, values in _values_read().items() if values}
    copied = []
    for name, text in _repository_files().items():
        for number, line in enumerate(text.splitlines(), start=1):
            found = _dates(line)
            if len(found) >= 2 and any(len(found & printed) >= 2 for printed in dates.values()):
                copied.append(f"{name}:{number}")
    assert not copied, f"lines holding two dates of one real document: {copied}"


@pytest.mark.real_data
@pytest.mark.skipif(not READINGS, reason="no reading of the real documents in cache/")
def test_no_number_of_a_real_document_is_written_in_the_repository() -> None:
    """A plate, an identification number, a bank or contract number read on a document never appears in the code, the
    settings, the docs or the tests (pieces of at least eight characters holding four digits or more)."""
    numbers = set()
    for values in _values_read().values():
        for value in values:
            for piece in re.split(r"[\n/;,]", value):
                key = _bare(piece)
                if len(key) >= 8 and sum(char.isdigit() for char in key) >= 4:
                    numbers.add(key)
    written = []
    for name, text in _repository_files().items():
        content = _bare(text)
        if any(number in content for number in numbers):
            written.append(name)
    assert not written, f"files holding a number read on a real document: {written}"
