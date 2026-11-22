"""Settings: defaults, overrides from .env and environment variables, path handling."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import REPO_ROOT, make_settings
from fraudcheck.ingest.settings import IngestSettings, find_repo_root, load_settings, read_dotenv


def test_defaults_match_the_documented_values() -> None:
    settings = IngestSettings()
    assert settings.pdf_dpi == 200
    assert settings.max_side_px == 3000
    assert settings.max_bytes == 5_000_000
    assert settings.text_min_chars == 20
    assert settings.reader_formats == ("png", "jpeg", "webp", "gif")
    assert settings.root == REPO_ROOT


def test_repo_root_is_found_from_the_package_not_the_current_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert find_repo_root() == REPO_ROOT
    assert load_settings(use_dotenv=False).documents_path == REPO_ROOT / "data" / "documents" / "documents"


def test_environment_variables_override_defaults() -> None:
    settings = load_settings(
        environ={"INGEST_PDF_DPI": "300", "INGEST_READER_FORMATS": "PNG, jpg", "OTHER": "x"}, use_dotenv=False
    )
    assert settings.pdf_dpi == 300
    assert settings.reader_formats == ("png", "jpeg")


def test_dotenv_is_read_for_ingest_keys_only(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "# comment\nAZURE_CLIENT_SECRET=not-for-us\nINGEST_MAX_PAGES=7\nexport INGEST_JPEG_QUALITY='80'\n",
        encoding="utf-8",
    )
    settings = load_settings({"root": tmp_path}, environ={})
    assert settings.max_pages == 7
    assert settings.jpeg_quality == 80


def test_priority_is_overrides_then_environment_then_dotenv(tmp_path) -> None:
    (tmp_path / ".env").write_text("INGEST_PDF_DPI=100\nINGEST_MAX_PAGES=3\nINGEST_TEXT_MIN_CHARS=5\n", encoding="utf-8")
    settings = load_settings({"root": tmp_path, "pdf_dpi": 150}, environ={"INGEST_PDF_DPI": "120", "INGEST_MAX_PAGES": "4"})
    assert (settings.pdf_dpi, settings.max_pages, settings.text_min_chars) == (150, 4, 5)


def test_invalid_values_are_refused() -> None:
    with pytest.raises(ValidationError):
        load_settings(environ={"INGEST_PDF_DPI": "fine"}, use_dotenv=False)
    with pytest.raises(ValidationError):
        IngestSettings(max_side_px=10)
    with pytest.raises(ValidationError):
        IngestSettings(unknown_setting=1)


def test_unknown_ingest_variable_is_reported(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        load_settings(environ={"INGEST_PDF_DPII": "300"}, use_dotenv=False)
    assert "INGEST_PDF_DPII" in caplog.text


def test_read_dotenv_handles_quotes_comments_and_missing_file(tmp_path) -> None:
    path = tmp_path / ".env"
    path.write_text('A="quoted value"\nB=plain # trailing comment\nnot a pair\n', encoding="utf-8")
    assert read_dotenv(path) == {"A": "quoted value", "B": "plain"}
    assert read_dotenv(tmp_path / "missing.env") == {}


def test_relative_paths_are_read_from_the_root_and_displayed_with_slashes(workspace) -> None:
    settings = make_settings(workspace)
    inside = settings.documents_path / "DOC-1.png"
    assert settings.cache_path == workspace / "cache"
    assert settings.display(inside) == "data/documents/DOC-1.png"
    assert settings.resolve("data/documents/DOC-1.png") == inside
    outside = workspace.parent / "elsewhere" / "DOC-2.png"
    assert settings.display(outside) == outside.as_posix()


def test_output_key_follows_output_settings_only(workspace) -> None:
    base = make_settings(workspace)
    assert make_settings(workspace, cache_dir=Path("other")).output_key() == base.output_key()
    assert make_settings(workspace, pdf_dpi=150).output_key() != base.output_key()
    assert make_settings(workspace, max_bytes=1_000_000).output_key() != base.output_key()
