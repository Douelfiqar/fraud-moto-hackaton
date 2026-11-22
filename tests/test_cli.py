"""The command line: summary of counts, exit status, and working from any directory."""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPO_ROOT, SAMPLE_TEXT, drawn_picture, make_catalogue, make_text_pdf, save_picture
from fraudcheck.cli import EXIT_DOCUMENT_ERRORS, EXIT_OK, EXIT_SETUP_ERROR, main


def arguments(workspace) -> list[str]:
    return [
        "ingest",
        "--documents", str(workspace / "data" / "documents"),
        "--catalogue", str(workspace / "data" / "catalogue.xlsx"),
        "--cache", str(workspace / "cache"),
    ]


def fill(workspace) -> None:
    folder = workspace / "data" / "documents"
    save_picture(drawn_picture(), folder / "DOC-001.png")
    make_text_pdf(folder / "DOC-002.pdf")
    make_catalogue(workspace / "data" / "catalogue.xlsx", [("DOC-001", "DOC-001.png", "Carte grise")])


def test_summary_gives_counts_and_no_document_text(workspace, capsys) -> None:
    fill(workspace)
    assert main(arguments(workspace)) == EXIT_OK
    out = capsys.readouterr().out
    assert "Ingest: 2 documents" in out
    assert "picture 1 | pdf_text 1" in out
    assert "carte_grise 1 | unknown 1" in out
    assert "warnings   1 documents: no_catalogue_row 1" in out
    assert "errors     none" in out
    assert SAMPLE_TEXT not in out and "Specimen" not in out


def test_second_run_says_everything_came_from_the_cache(workspace, capsys) -> None:
    fill(workspace)
    main(arguments(workspace))
    capsys.readouterr()
    assert main(arguments(workspace)) == EXIT_OK
    assert "(0 processed, 2 taken from the cache)" in capsys.readouterr().out


def test_one_document_and_verbose_listing(workspace, capsys) -> None:
    fill(workspace)
    assert main([*arguments(workspace), "DOC-002", "-v"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Ingest: 1 requested document " in out
    assert "DOC-002          no_catalogue_row" in out


def test_document_errors_give_exit_status_1(workspace, capsys) -> None:
    fill(workspace)
    (workspace / "data" / "documents" / "DOC-003.png").write_bytes(b"")
    assert main([*arguments(workspace), "-v"]) == EXIT_DOCUMENT_ERRORS
    out = capsys.readouterr().out
    assert "errors     empty_file 1" in out and "ERROR empty_file" in out


def test_setup_problems_give_exit_status_2(workspace, capsys) -> None:
    assert main(["ingest", "--documents", str(workspace / "missing")]) == EXIT_SETUP_ERROR
    assert "documents folder not found" in capsys.readouterr().err
    assert main([*arguments(workspace), "--dpi", "5"]) == EXIT_SETUP_ERROR
    assert "Invalid setting" in capsys.readouterr().err
    fill(workspace)
    (workspace / "cache").write_text("not a folder", encoding="utf-8")
    assert main(arguments(workspace)) == EXIT_SETUP_ERROR
    assert "cannot write" in capsys.readouterr().err


def test_module_runs_from_another_directory(workspace, tmp_path) -> None:
    fill(workspace)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("INGEST_")}
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    environment["PYTHONIOENCODING"] = "utf-8"  # the paths printed hold accents
    completed = subprocess.run(
        [sys.executable, "-m", "fraudcheck", *arguments(workspace)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == EXIT_OK, completed.stderr
    assert "Ingest: 2 documents" in completed.stdout
