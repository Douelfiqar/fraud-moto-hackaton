"""The whole step on a small generated folder: records, index, cache reuse and robustness."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from conftest import (
    drawn_picture,
    make_catalogue,
    make_image_pdf,
    make_settings,
    make_text_pdf,
    noise_picture,
    save_picture,
)
from fraudcheck.ingest import pipeline
from fraudcheck.ingest.catalogue import ConfigError
from fraudcheck.ingest.models import Code, DocumentRecord
from fraudcheck.ingest.pipeline import IngestSetupError, load_index, load_record, run_ingest, summarise
from fraudcheck.ingest.storage import CacheWriteError


@pytest.fixture
def mixed_folder(settings):
    """A folder with good documents, junk, damaged files and spreadsheet mismatches."""
    folder = settings.documents_path
    save_picture(drawn_picture((80, 60)), folder / "DOC-001.png")
    make_text_pdf(folder / "DOC-002.pdf")
    make_image_pdf(folder / "DOC-003.pdf")
    make_text_pdf(folder / "DOC-004.PDF", pages=3)
    save_picture(drawn_picture((50, 50)), folder / "DOC-005.JPG", format="JPEG")
    save_picture(drawn_picture((50, 50)), folder / "DOC-006.pdf", format="PNG")  # a picture named .pdf
    (folder / "DOC-007.pdf").write_bytes(b"%PDF-1.7\n" + os.urandom(500))  # damaged
    (folder / "DOC-008.png").write_bytes(b"")  # empty
    (folder / "DOC-009.docx").write_bytes(b"PK\x03\x04 not a picture")  # unsupported
    save_picture(drawn_picture(), folder / "DOC-010.png")  # absent from the spreadsheet
    save_picture(drawn_picture(), folder / "DOC-001.webp")  # same id as DOC-001.png
    save_picture(drawn_picture(), folder / "._DOC-001.png")  # junk
    (folder / "__MACOSX").mkdir()
    (folder / "__MACOSX" / "._DOC-002.pdf").write_bytes(b"junk")
    make_catalogue(
        settings.catalogue_file,
        [
            ("DOC-001", "DOC-001.png", "Carte grise"),
            ("DOC-002", "DOC-002.pdf", "Relevé d'information (relevé de sinistres)"),
            ("DOC-003", "DOC-003.pdf", "Relevé d'identité bancaire"),
            ("DOC-004", "DOC-004.pdf", "Permis de conduire nouveau modèle, recto"),
            ("DOC-005", "DOC-005.jpg", "Titre de séjour, recto"),
            ("DOC-006", "DOC-006.pdf", "Pièce d'identité"),
            ("DOC-007", "DOC-007.pdf", "Carte grise"),
            ("DOC-008", "DOC-008.png", "Carte grise"),
            ("DOC-009", "DOC-009.docx", "Facture"),
            ("DOC-099", "DOC-099.png", "Carte grise"),  # no file
        ],
    )
    return folder


def by_id(result) -> dict[str, DocumentRecord]:
    return {record.id: record for record in result.records}


def codes(record: DocumentRecord) -> list[Code]:
    return [notice.code for notice in record.warnings]


def test_full_run_produces_one_record_per_document_and_survives_bad_files(settings, mixed_folder) -> None:
    result = run_ingest(settings)
    records = by_id(result)
    assert sorted(records) == sorted(
        ["DOC-001", "DOC-001~2", "DOC-002", "DOC-003", "DOC-004", "DOC-005", "DOC-006", "DOC-007", "DOC-008",
         "DOC-009", "DOC-010"]
    )
    assert records["DOC-001"].kind == "picture" and records["DOC-001"].family == "carte_grise"
    assert records["DOC-001"].pages[0].picture == "data/documents/DOC-001.png"
    assert records["DOC-002"].kind == "pdf_text" and records["DOC-002"].family == "releve"
    assert records["DOC-002"].text == "cache/DOC-002/text.json" and records["DOC-002"].upright is True
    assert records["DOC-003"].kind == "pdf_scan" and records["DOC-003"].family == "rib"
    assert len(records["DOC-004"].pages) == 3 and records["DOC-004"].family == "licence"
    assert records["DOC-005"].kind == "picture" and records["DOC-005"].family == "identity"
    assert records["DOC-006"].kind == "picture" and Code.EXTENSION_MISMATCH in codes(records["DOC-006"])
    assert records["DOC-007"].error.code == Code.CORRUPT_FILE
    assert records["DOC-008"].error.code == Code.EMPTY_FILE
    assert records["DOC-009"].error.code == Code.UNSUPPORTED_FORMAT
    assert records["DOC-009"].family is None and Code.NATURE_UNMAPPED in codes(records["DOC-009"])
    assert records["DOC-010"].nature is None and codes(records["DOC-010"]) == [Code.NO_CATALOGUE_ROW]
    assert Code.DUPLICATE_ID in codes(records["DOC-001~2"]) and Code.DUPLICATE_ID in codes(records["DOC-001"])
    assert Code.CATALOGUE_FILE_MISMATCH in codes(records["DOC-001~2"])

    summary = summarise(result)
    assert summary.kinds == {"picture": 5, "pdf_text": 2, "pdf_scan": 1, "unknown": 3}
    assert summary.errors == {"corrupt_file": 1, "empty_file": 1, "unsupported_format": 1}
    assert summary.pages == 1 + 1 + 1 + 3 + 1 + 1 + 1 + 1


def test_index_lists_every_record_and_what_was_left_aside(settings, mixed_folder) -> None:
    run_ingest(settings)
    index = load_index(settings)
    assert [entry.id for entry in index.documents][:3] == ["DOC-001", "DOC-001~2", "DOC-002"]
    assert len(index.documents) == 11
    assert index.catalogue_rows_without_file == ["DOC-099"]
    assert {Path(item.path).name for item in index.ignored} == {"._DOC-001.png", "__MACOSX"}
    entry = next(entry for entry in index.documents if entry.id == "DOC-007")
    assert entry.error == Code.CORRUPT_FILE and entry.record == "cache/DOC-007/record.json"
    for entry in index.documents:
        assert load_record(settings, entry.id) is not None


def test_records_are_valid_json_with_portable_paths(settings, mixed_folder) -> None:
    run_ingest(settings)
    raw = json.loads((settings.cache_path / "DOC-004" / "record.json").read_text(encoding="utf-8"))
    record = DocumentRecord.model_validate(raw)
    assert record.source_file == "data/documents/DOC-004.PDF"
    assert all("\\" not in page.picture for page in record.pages)
    assert len(record.fingerprint) == 64
    assert record.fingerprint == hashlib.sha256((settings.documents_path / "DOC-004.PDF").read_bytes()).hexdigest()


def test_originals_are_never_modified_or_copied(settings, mixed_folder) -> None:
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in mixed_folder.glob("*.*")}
    run_ingest(settings)
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in mixed_folder.glob("*.*")}
    assert before == after
    cached = {path.name for path in settings.cache_path.rglob("*") if path.is_file()}
    assert not cached & set(before)


def test_second_run_reuses_the_cache_and_writes_nothing(settings, mixed_folder) -> None:
    first = run_ingest(settings)
    failed = {record.id for record in first.records if record.error}  # tried again on every run
    stamps = {
        path: path.stat().st_mtime_ns
        for path in settings.cache_path.rglob("*")
        if path.is_file() and path.name != "index.json" and path.parent.name not in failed
    }
    second = run_ingest(settings)
    assert second.reused == {record.id for record in first.records} - failed
    assert {path: path.stat().st_mtime_ns for path in stamps} == stamps
    assert [record.model_dump() for record in second.records if record.id not in failed] == [
        record.model_dump() for record in first.records if record.id not in failed
    ]


def test_force_settings_and_content_changes_make_records_again(settings, mixed_folder) -> None:
    run_ingest(settings)
    assert not run_ingest(settings, ["DOC-002"], force=True).reused
    finer = make_settings(settings.root, pdf_dpi=100)
    assert not run_ingest(finer, ["DOC-002"]).reused
    assert load_record(finer, "DOC-002").pages[0].dpi == 100.0
    make_text_pdf(settings.documents_path / "DOC-002.pdf", lines=("Changed content of the sample file",))
    assert not run_ingest(finer, ["DOC-002"]).reused


def test_spreadsheet_change_updates_nature_without_processing_again(settings, mixed_folder) -> None:
    run_ingest(settings)
    make_catalogue(settings.catalogue_file, [("DOC-001", "DOC-001.png", "Pièce d'identité")])
    result = run_ingest(settings, ["DOC-001", "DOC-002"])
    records = by_id(result)
    assert {"DOC-001", "DOC-002"} <= result.reused
    assert records["DOC-001"].family == "identity"
    assert records["DOC-002"].nature is None and Code.NO_CATALOGUE_ROW in codes(records["DOC-002"])
    assert load_record(settings, "DOC-001").family == "identity"


def test_stale_page_pictures_are_removed(settings) -> None:
    folder = settings.documents_path
    make_text_pdf(folder / "DOC-020.pdf", pages=3)
    run_ingest(settings)
    make_text_pdf(folder / "DOC-020.pdf", pages=1)
    run_ingest(settings)
    names = sorted(path.name for path in (settings.cache_path / "DOC-020").iterdir())
    assert names == ["page-1.png", "record.json", "text.json"]


def test_one_document_by_id_or_path_keeps_the_index_complete(settings, mixed_folder, monkeypatch, tmp_path) -> None:
    run_ingest(settings)
    single = run_ingest(settings, ["doc-003"])
    assert [record.id for record in single.records] == ["DOC-003"]
    assert len(load_index(settings).documents) == 11
    monkeypatch.chdir(mixed_folder)
    assert [record.id for record in run_ingest(settings, ["DOC-005.JPG"]).records] == ["DOC-005"]

    elsewhere = save_picture(drawn_picture(), tmp_path / "outside" / "DOC-003.png")
    result = run_ingest(settings, [str(elsewhere)])
    [record] = result.records
    assert record.id == "DOC-003~2" and record.source_file == elsewhere.as_posix()
    assert Code.OUTSIDE_DOCUMENTS_FOLDER in [notice.code for notice in result.notices]
    assert "DOC-003~2" not in [entry.id for entry in load_index(settings).documents]


def test_unknown_targets_are_reported(settings, mixed_folder) -> None:
    result = run_ingest(settings, ["DOC-001", "DOC-404"])
    assert [notice.code for notice in result.notices if notice.code == Code.TARGET_NOT_FOUND] == [Code.TARGET_NOT_FOUND]
    with pytest.raises(IngestSetupError):
        run_ingest(settings, ["DOC-404"])


def test_run_cannot_start_without_folder_or_family_rules(workspace) -> None:
    with pytest.raises(IngestSetupError):
        run_ingest(make_settings(workspace, documents_dir=Path("nowhere")))
    with pytest.raises(ConfigError):
        run_ingest(make_settings(workspace, families_path=Path("config/missing.toml")))


def test_missing_spreadsheet_leaves_natures_unknown(settings) -> None:
    save_picture(drawn_picture(), settings.documents_path / "DOC-030.png")
    result = run_ingest(settings)
    [record] = result.records
    assert record.nature is None and record.family is None and record.error is None
    assert codes(record) == [Code.NO_CATALOGUE_ROW]
    assert [notice.code for notice in result.notices] == [Code.CATALOGUE_UNAVAILABLE]


def test_unexpected_failure_on_one_document_does_not_stop_the_run(settings, monkeypatch) -> None:
    save_picture(drawn_picture(), settings.documents_path / "DOC-040.png")
    make_text_pdf(settings.documents_path / "DOC-041.pdf")

    def broken(*args, **kwargs):
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(pipeline, "process_picture", broken)
    records = by_id(run_ingest(settings))
    assert records["DOC-040"].error.code == Code.INTERNAL_ERROR
    assert records["DOC-041"].error is None and records["DOC-041"].kind == "pdf_text"


def test_cache_that_cannot_be_written_stops_the_run_clearly(settings) -> None:
    save_picture(drawn_picture(), settings.documents_path / "DOC-045.png")
    settings.cache_path.write_text("a file where the cache folder should be", encoding="utf-8")
    with pytest.raises(CacheWriteError, match="cannot write"):
        run_ingest(settings)


def test_no_temporary_file_is_left_behind(workspace) -> None:
    settings = make_settings(workspace, max_bytes=10_000)
    save_picture(noise_picture((150, 150)), settings.documents_path / "DOC-050.png")
    make_text_pdf(settings.documents_path / "DOC-051.pdf")
    run_ingest(settings)
    assert not [path for path in settings.cache_path.rglob("*") if path.suffix == ".tmp"]


def test_cache_inside_the_documents_folder_is_not_ingested(workspace) -> None:
    settings = make_settings(workspace, cache_dir=Path("data/documents/cache"), max_bytes=10_000)
    save_picture(noise_picture((120, 120)), settings.documents_path / "DOC-060.png")
    run_ingest(settings)
    assert (settings.cache_path / "DOC-060" / "page-1.jpg").is_file()
    result = run_ingest(settings)
    assert [record.id for record in result.records] == ["DOC-060"]
    assert not load_index(settings).ignored
