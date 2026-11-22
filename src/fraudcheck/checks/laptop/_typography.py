"""Helpers shared by the typography checks: pieces of text, printed lines, columns, page zones.

Everything here works on ONE document's own text layer. Positions are in pixels of the
page pictures; font sizes are in points, turned into pixels with each page's own dpi.
Evidence names pages and zones ("page 2, en bas à droite"), never what is written.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from fraudcheck.checks.base import DocumentContext
from fraudcheck.ingest.models import Page, TextSpan

_ROWS = ("en haut", "au milieu", "en bas")
_COLUMNS = ("à gauche", "au centre", "à droite")


@dataclass(frozen=True)
class Piece:
    """A visible piece of text with its size in pixels of its page picture."""

    span: TextSpan
    index: int
    px_per_pt: float

    @property
    def page(self) -> int:
        return self.span.page

    @property
    def size_px(self) -> float:
        return self.span.size * self.px_per_pt

    @property
    def chars(self) -> int:
        return sum(1 for ch in self.span.text if not ch.isspace())

    @property
    def height(self) -> float:
        return max(1e-6, self.span.bbox[3] - self.span.bbox[1])

    @property
    def baseline(self) -> float | None:
        return self.span.origin[1] if self.span.origin else None


def applies_to(context: DocumentContext) -> bool:
    """Typography checks need a text PDF with its text layer."""
    return context.record.kind == "pdf_text" and context.text_layer is not None and bool(context.text_layer.spans)


def pieces(context: DocumentContext, min_chars: int = 1, upright_only: bool = False) -> list[Piece]:
    """The document's visible pieces of text (optionally only on pages whose text runs left to right)."""
    pages = {page.number: page for page in context.record.pages}
    found = []
    for index, span in enumerate(context.text_layer.spans if context.text_layer else ()):
        page = pages.get(span.page)
        if not span.visible or page is None or (upright_only and page.upright is not True):
            continue
        piece = Piece(span, index, (page.dpi or 72.0) / 72.0)
        if piece.chars >= min_chars:
            found.append(piece)
    return found


def printed_lines(found: Iterable[Piece], min_overlap: float) -> list[list[Piece]]:
    """Group pieces of each page into printed lines: pieces whose heights overlap enough, left to right."""
    lines: list[list[Piece]] = []
    by_page: dict[int, list[Piece]] = {}
    for piece in found:
        by_page.setdefault(piece.page, []).append(piece)
    for page_pieces in by_page.values():
        current: list[Piece] = []
        for piece in sorted(page_pieces, key=lambda p: (p.span.bbox[1] + p.span.bbox[3]) / 2):
            if current and _vertical_overlap(current[0], piece) >= min_overlap:
                current.append(piece)
                continue
            if current:
                lines.append(sorted(current, key=lambda p: p.span.bbox[0]))
            current = [piece]
        if current:
            lines.append(sorted(current, key=lambda p: p.span.bbox[0]))
    return lines


def _vertical_overlap(one: Piece, two: Piece) -> float:
    top, bottom = max(one.span.bbox[1], two.span.bbox[1]), min(one.span.bbox[3], two.span.bbox[3])
    return max(0.0, bottom - top) / min(one.height, two.height)


def horizontal_gap(one: Piece, two: Piece) -> float:
    return max(0.0, two.span.bbox[0] - one.span.bbox[2], one.span.bbox[0] - two.span.bbox[2])


def line_neighbours(piece: Piece, line: Sequence[Piece], max_gap_em: float) -> list[Piece]:
    """Other pieces of the same printed line, close enough to belong to the same run of text."""
    return [other for other in line if other is not piece and horizontal_gap(piece, other) <= max_gap_em * piece.size_px]


def median(values: Iterable[float]) -> float:
    return statistics.median(list(values))


def agreed_value(values: Sequence[float], tolerance: float, minimum: int = 2) -> float | None:
    """The value most neighbours share (within ``tolerance``), or ``None`` when they do not agree.

    A piece can only be "off" its neighbours when they agree with each other: at least
    ``minimum`` of them, and more than half, share one value. Two pieces alone cannot tell
    which one is off, and a line whose sizes vary continuously has no usual size.
    """
    best: list[float] = []
    for value in values:
        group = [other for other in values if abs(other - value) <= tolerance]
        if len(group) > len(best):
            best = group
    if len(best) >= minimum and len(best) * 2 > len(values):
        return median(best)
    return None


def dominant(values: Iterable[float], digits: int = 2) -> float:
    counts = Counter(round(value, digits) for value in values)
    return counts.most_common(1)[0][0]


def zone(page: Page | None, bbox: Sequence[float]) -> str:
    """Where a box sits on its page, in words: 'en haut à droite', 'au centre'..."""
    if page is None:
        return "sur la page"
    x = (bbox[0] + bbox[2]) / 2 / max(1, page.width)
    y = (bbox[1] + bbox[3]) / 2 / max(1, page.height)
    row, column = _ROWS[min(2, int(y * 3))], _COLUMNS[min(2, int(x * 3))]
    if row == "au milieu" and column == "au centre":
        return "au centre"
    return f"{row} {column}"


def places(context: DocumentContext, boxes: Iterable[tuple[int, Sequence[float]]], limit: int = 4) -> str:
    """'page 1 en haut à droite, page 3 au centre et 2 autres endroits': where the findings are."""
    pages = {page.number: page for page in context.record.pages}
    named = list(dict.fromkeys(f"page {number} {zone(pages.get(number), bbox)}" for number, bbox in boxes))
    text = ", ".join(named[:limit])
    if len(named) > limit:
        text += f" et {len(named) - limit} autre(s) endroit(s)"
    return text


def box_area(bbox: Sequence[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def shared_area(one: Sequence[float], two: Sequence[float]) -> float:
    width = min(one[2], two[2]) - max(one[0], two[0])
    height = min(one[3], two[3]) - max(one[1], two[1])
    return max(0.0, width) * max(0.0, height)
