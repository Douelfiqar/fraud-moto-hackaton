"""The organisers' spreadsheet, which gives each document's nature, and the rules from nature to family."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fraudcheck.ingest.discover import organiser_id
from fraudcheck.ingest.models import FAMILIES, Code, Family, Notice

_ID_HEADERS = frozenset({"doc id", "id", "identifiant", "document id", "id document"})
_FILE_HEADERS = frozenset({"fichier", "file", "filename", "file name", "nom du fichier", "nom de fichier"})
_NATURE_HEADERS = frozenset({"nature", "nature du document", "type", "type de document", "document type"})
#: The header row is looked for in the first rows only, in case a title sits above it.
_HEADER_SEARCH_ROWS = 10


class ConfigError(RuntimeError):
    """A configuration file of the repository is missing or invalid."""


def normalise_text(value: str) -> str:
    """Lower-case, accents removed, straight apostrophes, single spaces: for comparing labels."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = text.casefold().replace("_", " ")
    return " ".join(text.split())


@dataclass(frozen=True)
class CatalogueRow:
    id: str
    file: str | None
    nature: str | None
    line: int


@dataclass
class Catalogue:
    """Rows of the spreadsheet, by id. Never raises: problems become notices."""

    path: Path
    available: bool = False
    rows: dict[str, CatalogueRow] = field(default_factory=dict)
    notices: list[Notice] = field(default_factory=list)

    def lookup(self, doc_id: str) -> CatalogueRow | None:
        return self.rows.get(doc_id.casefold())

    def ids_by_file(self) -> dict[str, str]:
        """Lower-case file name to id, for files whose name holds no id."""
        return {row.file.casefold(): row.id for row in self.rows.values() if row.file}

    def rows_without_file(self, present_ids: set[str]) -> list[str]:
        """Ids listed in the spreadsheet for which no file was found (``present_ids`` in lower case)."""
        return [row.id for key, row in self.rows.items() if key not in present_ids]


def load_catalogue(path: Path, sheet: str) -> Catalogue:
    """Read the spreadsheet. A missing or unreadable file gives an empty catalogue and a notice."""
    catalogue = Catalogue(path=path)
    if not path.is_file():
        catalogue.notices.append(_unavailable(f"spreadsheet not found: {path}"))
        return catalogue
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for a damaged or non-Excel file
        catalogue.notices.append(_unavailable(f"spreadsheet could not be read ({type(exc).__name__})"))
        return catalogue
    try:
        if sheet in workbook.sheetnames:
            worksheet = workbook[sheet]
        elif workbook.worksheets:
            worksheet = workbook.worksheets[0]
            catalogue.notices.append(
                Notice(
                    code=Code.CATALOGUE_SHEET_MISSING,
                    message=f"sheet '{sheet}' not found; read the first sheet '{worksheet.title}' instead",
                )
            )
        else:
            catalogue.notices.append(_unavailable("the spreadsheet has no sheet"))
            return catalogue
        _read_rows(catalogue, worksheet.iter_rows(values_only=True))
    except Exception as exc:
        catalogue.notices.append(_unavailable(f"spreadsheet could not be read ({type(exc).__name__})"))
        catalogue.rows.clear()
        catalogue.available = False
    finally:
        workbook.close()
    return catalogue


def _unavailable(message: str) -> Notice:
    return Notice(code=Code.CATALOGUE_UNAVAILABLE, message=message)


def _read_rows(catalogue: Catalogue, rows: Any) -> None:
    columns: dict[str, int | None] | None = None
    for line, values in enumerate(rows, start=1):
        if columns is None:
            columns = _find_columns(values)
            if columns is None and line >= _HEADER_SEARCH_ROWS:
                break
            continue
        _add_row(catalogue, columns, values, line)
    if columns is None:
        catalogue.notices.append(
            _unavailable("no header row with an id or file column and a nature column was found")
        )
    else:
        catalogue.available = True


def _find_columns(values: Sequence[Any]) -> dict[str, int | None] | None:
    columns: dict[str, int | None] = {"id": None, "file": None, "nature": None}
    for index, value in enumerate(values):
        if value is None:
            continue
        header = normalise_text(str(value))
        if header in _ID_HEADERS and columns["id"] is None:
            columns["id"] = index
        elif header in _FILE_HEADERS and columns["file"] is None:
            columns["file"] = index
        elif (header in _NATURE_HEADERS or header.startswith("nature")) and columns["nature"] is None:
            columns["nature"] = index
    has_key = columns["id"] is not None or columns["file"] is not None
    return columns if has_key and columns["nature"] is not None else None


def _cell(values: Sequence[Any], index: int | None) -> str | None:
    if index is None or index >= len(values) or values[index] is None:
        return None
    value = values[index]
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(unicodedata.normalize("NFC", str(value)).split())
    return text or None


def _add_row(catalogue: Catalogue, columns: dict[str, int | None], values: Sequence[Any], line: int) -> None:
    raw_id = _cell(values, columns["id"])
    file_name = _cell(values, columns["file"])
    nature = _cell(values, columns["nature"])
    if raw_id is None and file_name is None:
        return  # blank line
    doc_id = organiser_id(raw_id) or raw_id or organiser_id(file_name) or Path(file_name or "").stem
    if not doc_id:
        return
    key = doc_id.casefold()
    if key in catalogue.rows:
        first = catalogue.rows[key]
        detail = "" if first.nature == nature else " with a different nature"
        catalogue.notices.append(
            Notice(
                code=Code.CATALOGUE_DUPLICATE_ROW,
                message=f"{doc_id} is listed again on row {line}{detail}; row {first.line} is used",
            )
        )
        return
    catalogue.rows[key] = CatalogueRow(id=doc_id, file=file_name, nature=nature, line=line)


@dataclass(frozen=True)
class FamilyRule:
    family: Family
    patterns: tuple[re.Pattern[str], ...]


def load_family_rules(path: Path) -> list[FamilyRule]:
    """Read ``config/families.toml``. Raises ``ConfigError`` when it is missing or invalid."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"family rules not found: {path}") from exc
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"family rules could not be read: {path} ({exc})") from exc
    entries = data.get("family")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} needs at least one [[family]] table")
    rules = []
    for number, entry in enumerate(entries, start=1):
        name = entry.get("name") if isinstance(entry, dict) else None
        phrases = entry.get("phrases") if isinstance(entry, dict) else None
        if name not in FAMILIES:
            raise ConfigError(f"{path}, rule {number}: unknown family {name!r}; expected one of {FAMILIES}")
        if not isinstance(phrases, list) or not phrases or not all(isinstance(p, str) and p.strip() for p in phrases):
            raise ConfigError(f"{path}, rule {number}: 'phrases' must be a list of non-empty strings")
        patterns = tuple(re.compile(r"(?<!\w)" + re.escape(normalise_text(p)) + r"(?!\w)") for p in phrases)
        rules.append(FamilyRule(family=name, patterns=patterns))
    return rules


def family_for(nature: str | None, rules: Sequence[FamilyRule]) -> Family | None:
    """The family of the first rule with a phrase found in the nature, or ``None``."""
    if not nature:
        return None
    text = normalise_text(nature)
    for rule in rules:
        if any(pattern.search(text) for pattern in rule.patterns):
            return rule.family
    return None
