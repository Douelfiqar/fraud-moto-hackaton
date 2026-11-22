"""The organisers' spreadsheet and the rules from nature to family."""

from __future__ import annotations

import pytest

from conftest import FAMILIES_FILE, make_catalogue
from fraudcheck.ingest.catalogue import ConfigError, family_for, load_catalogue, load_family_rules
from fraudcheck.ingest.models import Code

# Generic labels of French document types, as written in the organisers' spreadsheet.
NATURE_FAMILIES = {
    "Carte grise": "carte_grise",
    "Permis de conduire nouveau modèle, recto": "licence",
    "Permis de conduire nouveau modèle, verso": "licence",
    "Permis de conduire ancien modèle, recto": "licence",
    "Permis de conduire étranger, verso": "licence",
    "Relevé d'information (relevé de sinistres)": "releve",
    "Relevé d'identité bancaire": "rib",
    "Titre de séjour, recto": "identity",
    "Titre de séjour, verso": "identity",
    "Attestation de demande d'asile": "identity",
    "Attestation de prolongation d'instruction d'un titre de séjour": "identity",
    "Pièce d'identité": "identity",
}


@pytest.fixture(scope="module")
def rules():
    return load_family_rules(FAMILIES_FILE)


@pytest.mark.parametrize(("nature", "family"), sorted(NATURE_FAMILIES.items()))
def test_each_known_nature_maps_to_its_family(rules, nature, family) -> None:
    assert family_for(nature, rules) == family


def test_family_matching_ignores_accents_case_and_apostrophe_style(rules) -> None:
    assert family_for("RELEVE D’IDENTITE BANCAIRE", rules) == "rib"
    assert family_for("  carte   GRISE ", rules) == "carte_grise"


def test_unknown_or_missing_nature_gives_no_family(rules) -> None:
    assert family_for("Facture d'électricité", rules) is None
    assert family_for("Distribution", rules) is None  # "rib" only matches as a whole word
    assert family_for(None, rules) is None
    assert family_for("", rules) is None


def test_family_rules_file_errors_are_reported(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_family_rules(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[[family]]\nname = 'boat'\nphrases = ['bateau']\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown family"):
        load_family_rules(bad)
    bad.write_text("this is not toml = = =", encoding="utf-8")
    with pytest.raises(ConfigError, match="could not be read"):
        load_family_rules(bad)
    bad.write_text("[[family]]\nname = 'rib'\nphrases = []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="phrases"):
        load_family_rules(bad)


def test_rows_are_read_by_id(tmp_path) -> None:
    path = make_catalogue(
        tmp_path / "c.xlsx",
        [("DOC-001", "DOC-001.png", "Carte grise"), ("doc-002", "DOC-002.pdf", " Relevé  d'identité bancaire ")],
    )
    catalogue = load_catalogue(path, "Documents")
    assert catalogue.available and not catalogue.notices
    assert catalogue.lookup("DOC-001").nature == "Carte grise"
    row = catalogue.lookup("doc-002")
    assert row.id == "DOC-002" and row.nature == "Relevé d'identité bancaire"
    assert catalogue.ids_by_file() == {"doc-001.png": "DOC-001", "doc-002.pdf": "DOC-002"}


def test_header_row_is_found_below_a_title_and_ids_come_from_files_when_missing(tmp_path) -> None:
    path = make_catalogue(
        tmp_path / "c.xlsx",
        [(None, "DOC-010.png", "Carte grise"), ("DOC-011", None, None), (None, None, None)],
        title="Liste des documents",
    )
    catalogue = load_catalogue(path, "Documents")
    assert catalogue.lookup("DOC-010").file == "DOC-010.png"
    assert catalogue.lookup("DOC-011").nature is None
    assert len(catalogue.rows) == 2


def test_duplicate_rows_keep_the_first_and_are_reported(tmp_path) -> None:
    path = make_catalogue(tmp_path / "c.xlsx", [("DOC-1", "a", "Carte grise"), ("DOC-1", "b", "Pièce d'identité")])
    catalogue = load_catalogue(path, "Documents")
    assert catalogue.lookup("DOC-1").file == "a"
    assert [notice.code for notice in catalogue.notices] == [Code.CATALOGUE_DUPLICATE_ROW]


def test_other_sheet_name_falls_back_to_the_first_sheet(tmp_path) -> None:
    path = make_catalogue(tmp_path / "c.xlsx", [("DOC-1", "DOC-1.png", "Carte grise")], sheet="Feuil1")
    catalogue = load_catalogue(path, "Documents")
    assert catalogue.lookup("DOC-1") is not None
    assert [notice.code for notice in catalogue.notices] == [Code.CATALOGUE_SHEET_MISSING]


@pytest.mark.parametrize("content", [None, b"not a spreadsheet at all", b""])
def test_missing_or_damaged_spreadsheet_gives_an_empty_catalogue(tmp_path, content) -> None:
    path = tmp_path / "c.xlsx"
    if content is not None:
        path.write_bytes(content)
    catalogue = load_catalogue(path, "Documents")
    assert not catalogue.available and not catalogue.rows
    assert [notice.code for notice in catalogue.notices] == [Code.CATALOGUE_UNAVAILABLE]


def test_spreadsheet_without_usable_headers_is_unavailable(tmp_path) -> None:
    path = make_catalogue(tmp_path / "c.xlsx", [("a", "b", "c")], headers=("colonne 1", "colonne 2", "colonne 3"))
    catalogue = load_catalogue(path, "Documents")
    assert not catalogue.available
    assert catalogue.notices[0].code == Code.CATALOGUE_UNAVAILABLE


def test_rows_without_file_are_listed(tmp_path) -> None:
    path = make_catalogue(tmp_path / "c.xlsx", [("DOC-1", "DOC-1.png", "Carte grise"), ("DOC-2", "DOC-2.png", "Carte grise")])
    catalogue = load_catalogue(path, "Documents")
    assert catalogue.rows_without_file({"doc-1"}) == ["DOC-2"]
