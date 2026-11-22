"""Each document is judged on its own: the rule, made structural.

A check receives one document's own record, text layer, reading and inspection. These
tests fail if a check module reaches for the index, the cache folder or another
document, or if running the checks on one document opens another document's files.
"""

from __future__ import annotations

import ast
import builtins
import io
import pathlib
from pathlib import Path

from conftest import REPO_ROOT
from fraudcheck.checks.base import CHECK_PACKAGES, registered
from fraudcheck.checks.runner import check_document, document_context
from fraudcheck.checks.settings import CheckSettings
from fraudcheck.ingest.pipeline import load_record

CHECK_FOLDERS = [REPO_ROOT / "src" / Path(*name.split(".")) for name in CHECK_PACKAGES]
#: Names a check module must never use: they reach the index, the cache or other documents.
FORBIDDEN_NAMES = {
    "load_index", "read_index", "load_record", "read_record", "load_reading", "load_inspection", "load_results",
    "run_ingest", "discover", "cache_path", "cache_dir", "documents_path", "IngestSettings", "load_settings",
    "iterdir", "glob", "rglob", "walk", "listdir", "scandir",
}
FORBIDDEN_TEXT = ("index.json", "results.json", "checks.json", "copie retouchée", "look_alike")


def check_modules() -> list[Path]:
    return [path for folder in CHECK_FOLDERS for path in folder.glob("*.py")]


def test_check_modules_never_reach_the_index_the_cache_or_other_documents() -> None:
    assert check_modules(), "no check module found"
    for path in check_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        used |= {alias.name.split(".")[-1] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        assert not used & FORBIDDEN_NAMES, (path.name, sorted(used & FORBIDDEN_NAMES))
        text = path.read_text(encoding="utf-8")
        assert not [word for word in FORBIDDEN_TEXT if word in text], path.name


def test_running_the_checks_on_one_document_opens_none_of_the_others(family_folder, monkeypatch) -> None:
    settings = CheckSettings(root=family_folder.root, doc_types_dir=REPO_ROOT / "config" / "doc_types")
    record = load_record(family_folder, "DOC-202")
    context = document_context(family_folder, settings, record)  # the runner gathers the document's own files
    opened: list[str] = []
    real_open, real_path_open = builtins.open, pathlib.Path.open

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_path_open(self, *args, **kwargs):
        opened.append(str(self))
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "open", spy_path_open)
    answers = check_document(context, registered())
    assert "font" in answers
    cache = str(family_folder.cache_path)
    reached = [path for path in opened if path.startswith(cache) or "data" in Path(path).parts]
    assert reached == [], reached  # checks open no file of the cache or of the documents folder


def test_the_context_holds_one_document_only(family_folder) -> None:
    settings = CheckSettings(root=family_folder.root)
    context = document_context(family_folder, settings, load_record(family_folder, "DOC-202"))
    assert context.record.id == "DOC-202"
    assert context.text_layer is not None and context.text_layer.id == "DOC-202"
    assert not hasattr(context, "ingest") and not hasattr(context, "index")
