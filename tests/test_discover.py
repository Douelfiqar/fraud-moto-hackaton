"""Finding documents: junk files, ids from file names, duplicates."""

from __future__ import annotations

from pathlib import Path

from fraudcheck.ingest.discover import discover, natural_key, organiser_id, safe_id
from fraudcheck.ingest.models import Code


def touch(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_organiser_id_is_read_from_names() -> None:
    assert organiser_id("DOC-707") == "DOC-707"
    assert organiser_id("doc_042 (copie)") == "DOC-042"
    assert organiser_id("DOC707") == "DOC-707"
    assert organiser_id("scan-DOC-12-recto") == "DOC-12"
    assert organiser_id("XDOC-1") is None
    assert organiser_id("photo") is None


def test_safe_id_survives_any_name() -> None:
    assert safe_id("Pièce d'identité recto") == "Piece_d_identite_recto"
    assert safe_id("CON") == "_CON"
    assert safe_id("...") == "document"


def test_natural_order_puts_short_numbers_first() -> None:
    assert sorted(["DOC-100", "DOC-99", "DOC-7"], key=natural_key) == ["DOC-7", "DOC-99", "DOC-100"]


def test_junk_files_and_folders_are_ignored(tmp_path) -> None:
    touch(tmp_path / "DOC-001.png")
    touch(tmp_path / "._DOC-001.png")
    touch(tmp_path / ".DS_Store")
    touch(tmp_path / "Thumbs.db")
    touch(tmp_path / "~$catalogue.xlsx")
    touch(tmp_path / "upload.crdownload")
    touch(tmp_path / "__MACOSX" / "._DOC-001.png")
    touch(tmp_path / ".hidden" / "DOC-009.png")
    found = discover(tmp_path)
    assert [candidate.id for candidate in found.candidates] == ["DOC-001"]
    reasons = {path.name: reason for path, reason in found.ignored}
    assert set(reasons) == {"._DOC-001.png", ".DS_Store", "Thumbs.db", "~$catalogue.xlsx", "upload.crdownload",
                            "__MACOSX", ".hidden"}


def test_ids_come_from_names_with_any_case_and_sub_folders(tmp_path) -> None:
    touch(tmp_path / "DOC-002.PNG")
    touch(tmp_path / "arrivals" / "doc_003.Pdf")
    found = discover(tmp_path)
    assert sorted(candidate.id for candidate in found.candidates) == ["DOC-002", "DOC-003"]
    assert all(not candidate.warnings for candidate in found.candidates)


def test_name_without_id_uses_the_spreadsheet_or_the_name(tmp_path) -> None:
    touch(tmp_path / "scan recto.png")
    touch(tmp_path / "listed.png")
    found = discover(tmp_path, name_to_id={"listed.png": "DOC-050"})
    by_name = {candidate.path.name: candidate for candidate in found.candidates}
    assert by_name["listed.png"].id == "DOC-050" and not by_name["listed.png"].warnings
    assert by_name["scan recto.png"].id == "scan_recto"
    assert [notice.code for notice in by_name["scan recto.png"].warnings] == [Code.NO_ID_IN_NAME]


def test_two_files_with_the_same_id_both_get_a_record(tmp_path) -> None:
    touch(tmp_path / "DOC-005.pdf")
    touch(tmp_path / "DOC-005.png")
    touch(tmp_path / "DOC-006.png")
    found = discover(tmp_path)
    ids = {candidate.path.name: candidate.id for candidate in found.candidates}
    assert ids == {"DOC-005.pdf": "DOC-005", "DOC-005.png": "DOC-005~2", "DOC-006.png": "DOC-006"}
    for candidate in found.candidates:
        codes = [notice.code for notice in candidate.warnings]
        assert codes == ([Code.DUPLICATE_ID] if candidate.base_id == "DOC-005" else [])


def test_excluded_folder_is_never_entered(tmp_path) -> None:
    touch(tmp_path / "DOC-001.png")
    touch(tmp_path / "cache" / "DOC-001" / "page-1.jpg")
    found = discover(tmp_path, exclude=[tmp_path / "cache"])
    assert [candidate.path.name for candidate in found.candidates] == ["DOC-001.png"]
    assert not found.ignored
