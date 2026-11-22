"""Find the documents in a folder, skip junk files, and give each document its id."""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from fraudcheck.ingest.models import Code, Notice

#: Organisers' ids look like DOC-042. Variants such as doc_707 or DOC707 are written DOC-042.
ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])DOC[-_ ]?(\d+)(?!\d)", re.IGNORECASE)
#: Marks a further file carrying an id already taken: DOC-042~2, DOC-042~3...
DUPLICATE_MARK = "~"
_MAX_ID_LENGTH = 80

_SYSTEM_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "icon\r", ".localized"})
_TEMPORARY_SUFFIXES = frozenset({".tmp", ".part", ".crdownload", ".partial"})
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


@dataclass(frozen=True)
class Candidate:
    """A file taken to be a document, with the id it will be stored under."""

    path: Path
    id: str
    base_id: str
    warnings: tuple[Notice, ...] = ()


@dataclass
class Discovery:
    candidates: list[Candidate] = field(default_factory=list)
    ignored: list[tuple[Path, str]] = field(default_factory=list)


def organiser_id(text: str | None) -> str | None:
    """Return the organisers' id found in a name (``DOC-042``), or ``None``."""
    if not text:
        return None
    match = ID_PATTERN.search(text)
    return f"DOC-{match.group(1)}" if match else None


def safe_id(stem: str) -> str:
    """Turn any file name into an id usable as a folder name on every system."""
    text = unicodedata.normalize("NFKD", stem)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")[:_MAX_ID_LENGTH] or "document"
    if text.split(".")[0].lower() in _WINDOWS_RESERVED:
        text = "_" + text
    return text


def natural_key(text: str) -> list[object]:
    """Sort key that puts DOC-9 before DOC-10."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def file_junk_reason(name: str) -> str | None:
    """Why a file is not a document (operating-system leftovers), or ``None`` for a real candidate."""
    lowered = name.casefold()
    if name.startswith("._"):
        return "macOS resource file (._)"
    if lowered in _SYSTEM_FILES:
        return "system file"
    if name.startswith("~$"):
        return "Office lock file"
    if name.startswith("."):
        return "hidden file"
    if Path(lowered).suffix in _TEMPORARY_SUFFIXES:
        return "temporary or unfinished file"
    return None


def folder_junk_reason(name: str) -> str | None:
    if name.casefold() == "__macosx":
        return "macOS archive folder (__MACOSX)"
    if name.startswith("."):
        return "hidden folder"
    if name == "__pycache__":
        return "Python cache folder"
    return None


def same_place(path: Path) -> str:
    """A form of the path in which two spellings of the same file compare equal."""
    return os.path.normcase(os.path.abspath(path))


def discover(
    folder: Path,
    *,
    exclude: Iterable[Path] = (),
    name_to_id: Mapping[str, str] | None = None,
) -> Discovery:
    """List the document files under ``folder`` (sub-folders included), in a stable order.

    ``exclude`` holds folders never to enter (the cache, when it sits inside the documents
    folder). ``name_to_id`` maps lower-case file names to ids, used when a name holds no id.
    """
    excluded = {same_place(path) for path in exclude}
    files: list[Path] = []
    ignored: list[tuple[Path, str]] = []

    def on_error(error: OSError) -> None:
        reason = error.strerror or type(error).__name__
        ignored.append((Path(error.filename or folder), f"could not be listed ({reason})"))

    for current, folders, names in os.walk(folder, onerror=on_error):
        here = Path(current)
        kept = []
        for name in sorted(folders, key=str.casefold):
            path = here / name
            if same_place(path) in excluded:
                continue
            if reason := folder_junk_reason(name):
                ignored.append((path, reason))
            else:
                kept.append(name)
        folders[:] = kept
        for name in sorted(names, key=str.casefold):
            path = here / name
            if reason := file_junk_reason(name):
                ignored.append((path, reason))
            else:
                files.append(path)
    return Discovery(candidates=assign_ids(files, name_to_id), ignored=ignored)


def assign_ids(paths: Iterable[Path], name_to_id: Mapping[str, str] | None = None) -> list[Candidate]:
    """Give each file an id; files sharing an id get ``~2``, ``~3``... after the first one."""
    name_to_id = name_to_id or {}
    found: list[tuple[Path, str, list[Notice]]] = []
    for path in paths:
        notes: list[Notice] = []
        base = organiser_id(path.stem) or name_to_id.get(path.name.casefold())
        if base is None:
            base = safe_id(path.stem)
            notes.append(
                Notice(
                    code=Code.NO_ID_IN_NAME,
                    message=f"the file name holds no organisers' id (DOC-...); id '{base}' made from the name",
                )
            )
        found.append((path, base, notes))

    groups: dict[str, list[int]] = defaultdict(list)
    for position, (_, base, _) in enumerate(found):
        groups[base.casefold()].append(position)

    ids: dict[int, str] = {}
    for members in groups.values():
        members.sort(key=lambda position: str(found[position][0]).casefold())
        names = ", ".join(found[position][0].name for position in members)
        for rank, position in enumerate(members, start=1):
            base = found[position][1]
            ids[position] = base if rank == 1 else f"{base}{DUPLICATE_MARK}{rank}"
            if len(members) > 1:
                found[position][2].append(
                    Notice(
                        code=Code.DUPLICATE_ID,
                        message=f"{len(members)} files carry the id {base} ({names}); "
                        f"this one is stored as {ids[position]}",
                    )
                )
    return [
        Candidate(path=path, id=ids[position], base_id=base, warnings=tuple(notes))
        for position, (path, base, notes) in enumerate(found)
    ]
