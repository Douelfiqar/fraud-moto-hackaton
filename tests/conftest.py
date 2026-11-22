"""Builders for small test files. Every fixture is generated here: no real document is used."""

from __future__ import annotations

import io
import os
import random
from collections.abc import Iterable, Sequence
from pathlib import Path

import pymupdf
import pytest
from openpyxl import Workbook
from PIL import Image, ImageDraw

from fraudcheck.ingest.pipeline import run_ingest
from fraudcheck.ingest.settings import IngestSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
FAMILIES_FILE = REPO_ROOT / "config" / "families.toml"
SAMPLE_TEXT = "Specimen document generated for automated tests"
RARE_TEXT = "Rare font sample"
HEADERS = ("doc_id", "fichier", "nature du document")


_DEVELOPER_PREFIXES = ("INGEST_", "CHECK_", "READ_", "INSPECT_", "DECIDE_", "AZURE_")
_DOTENV_READERS = ("fraudcheck.config", "fraudcheck.read.gateway", "fraudcheck.ingest.settings")


@pytest.fixture(autouse=True)
def _no_developer_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never pick up a developer's variables, nor the repository's own ``.env``.

    That file holds the team's real Azure identifiers: a test that read it could call
    the paid service. A ``.env`` a test writes in its own temporary folder is still read.
    """
    for key in list(os.environ):
        if key.upper().startswith(_DEVELOPER_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    # The team's own review decisions (data/review_decisions.csv) name real documents: tests never read them.
    monkeypatch.setenv("DECIDE_REVIEW_DECISIONS_PATH", "tests-have-no-review-decisions.csv")
    from fraudcheck import config

    real_reader, real_file = config.read_dotenv, (REPO_ROOT / config.ENV_FILE).resolve()

    def read_dotenv(path: Path) -> dict[str, str]:
        return {} if Path(path).resolve() == real_file else real_reader(path)

    for module in _DOTENV_READERS:
        monkeypatch.setattr(f"{module}.read_dotenv", read_dotenv)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A repository-like folder whose path holds spaces and accents."""
    root = tmp_path / "dossier équipe à tester"
    (root / "data" / "documents").mkdir(parents=True)
    return root


@pytest.fixture
def settings(workspace: Path) -> IngestSettings:
    return make_settings(workspace)


def make_settings(root: Path, **changes: object) -> IngestSettings:
    values: dict[str, object] = {
        "root": root,
        "documents_dir": Path("data/documents"),
        "catalogue_path": Path("data/catalogue.xlsx"),
        "families_path": FAMILIES_FILE,
        "cache_dir": Path("cache"),
    }
    values.update(changes)
    return IngestSettings(**values)


def documents(settings: IngestSettings) -> Path:
    return settings.documents_path


# Pictures


def drawn_picture(size: tuple[int, int] = (64, 48), mode: str = "RGB") -> Image.Image:
    """White picture with a black bar, so content can be checked after conversion."""
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).rectangle((size[0] // 4, size[1] // 3, size[0] * 3 // 4, size[1] * 2 // 3), fill="black")
    return image if mode == "RGB" else image.convert(mode)


def noise_picture(size: tuple[int, int]) -> Image.Image:
    """Random pixels: compresses badly, to make a file heavy while staying small in pixels."""
    return Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))


def save_picture(image: Image.Image, path: Path, format: str | None = None, **options: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format=format, **options)
    return path


def picture_bytes(size: tuple[int, int] = (200, 150)) -> bytes:
    buffer = io.BytesIO()
    drawn_picture(size).save(buffer, format="PNG")
    return buffer.getvalue()


# PDFs


def make_text_pdf(
    path: Path,
    pages: int = 1,
    lines: Sequence[str] = (SAMPLE_TEXT, "Second line of neutral sample words"),
    rare: str | None = RARE_TEXT,
) -> Path:
    """A PDF whose text is mostly Helvetica, with one short piece in Courier."""
    document = pymupdf.open()
    for _ in range(pages):
        page = document.new_page(width=595, height=842)
        for row, line in enumerate(lines):
            page.insert_text((72, 100 + 30 * row), line, fontname="helv", fontsize=12)
        if rare:
            page.insert_text((72, 100 + 30 * len(lines)), rare, fontname="cour", fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def make_image_pdf(path: Path, pages: int = 1) -> Path:
    """A PDF holding only a picture on each page, like a scan."""
    document = pymupdf.open()
    stream = picture_bytes()
    for _ in range(pages):
        page = document.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(50, 50, 545, 420), stream=stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def make_pdf(path: Path, build, **save_options: object) -> Path:
    """A one-page A4 PDF built by ``build(page)``."""
    document = pymupdf.open()
    build(document.new_page(width=595, height=842))
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path, **save_options)
    return path


# Spreadsheet


def make_catalogue(
    path: Path,
    rows: Iterable[Sequence[object]],
    *,
    sheet: str = "Documents",
    headers: Sequence[str] = HEADERS,
    title: str | None = None,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    if title:
        worksheet.append([title])
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


# A small set of documents for the checks and the decision (step 4 and later)


WORDS = "assurance contrat moto relevé date garantie conducteur véhicule souscription situation période".split()
#: Font used in tests as a stand-in for an editing tool's fallback font (set CHECK_FALLBACK_FONTS to it).
STAND_IN_FALLBACK = "Courier"


def filler(seed: int) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.choice(WORDS) for _ in range(rng.randint(7, 9))) for _ in range(32)]


def text_pdf(path, odd_line: int | None = None, odd_text: str = "Texte remplacé dans une autre police", seed: int = 1):
    """A one-page text PDF in Helvetica; ``odd_line`` is set in Courier instead."""
    lines = filler(seed)
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for row, line in enumerate(lines):
        if row == odd_line:
            page.insert_text((60, 70 + row * 22), odd_text, fontname="cour", fontsize=11)
        else:
            page.insert_text((60, 70 + row * 22), line, fontname="helv", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


@pytest.fixture
def family_folder(workspace):
    """Two pictures and three text PDFs, one with a line in another font (DOC-202), all ingested."""
    settings = make_settings(workspace)
    folder = settings.documents_path
    save_picture(drawn_picture((300, 200)), folder / "DOC-101.png")
    save_picture(drawn_picture((200, 300)), folder / "DOC-102.png")
    text_pdf(folder / "DOC-201.pdf")
    text_pdf(folder / "DOC-202.pdf", odd_line=12)
    text_pdf(folder / "DOC-203.pdf", seed=7)
    natures = {
        "DOC-101": "Carte grise", "DOC-102": "Pièce d'identité",
        "DOC-201": "Relevé d'information (relevé de sinistres)", "DOC-202": "Relevé d'information (relevé de sinistres)",
        "DOC-203": "Relevé d'information (relevé de sinistres)",
    }
    make_catalogue(settings.catalogue_file, [(doc_id, None, nature) for doc_id, nature in natures.items()])
    run_ingest(settings)
    return settings
