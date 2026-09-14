"""Turning a document's PDF into the page images a vision backend reads.

The simplest thing that is correct: every page rendered at a fixed long edge,
in colour, with the page's own orientation applied. Phase 1 owns the refining,
deskewing and the rest; this is the version the first benchmark row is measured
on, and what matters here is that it is the same for every backend.

Orientation comes from the file rather than from a guess. A PDF page carries a
`/Rotate` entry, and pdfium applies it in both `get_size` and `render`: page 0
of one val-split document reports a rotation of 90, a size of 792 by 612 where
the paper is 612 by 792, and renders 792 pixels wide. 12 of the 514 pages in a
400-document sample of the val split are rotated, so this is not a hypothetical.

Colour is kept. A grayscale page is smaller and cheaper and loses the one cue
that separates a stamp, a handwritten correction and a printed total on the
scans this corpus is full of, and rule 3's answer to "would grayscale be
cheaper" is to measure it as its own row rather than to assume it.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

LONG_EDGE = 1600
"""Pixels on the longer side of a rendered page.

Enough to read the small print on a scanned invoice, and well inside the 20MB
a request may carry inline. What it costs is measured rather than assumed: a
vision model bills a page by the tiles it is cut into, so the run records the
tokens every page actually became.
"""

MIME_TYPE = "image/png"
"""PNG, so a rendered page is compared byte for byte with itself across runs.

A lossy encoder would put its own artefacts between the page and the model, and
a benchmark that changes when the encoder is upgraded is not a benchmark.
"""


class PageError(Exception):
    """The document's pages cannot be read or rendered."""


@dataclass(frozen=True)
class PageImage:
    """One rendered page, ready to be sent."""

    number: int
    """1-based, as a human counts pages."""
    png: bytes
    width: int
    height: int

    @property
    def size(self) -> int:
        return len(self.png)


def render(pdf: Path, long_edge: int = LONG_EDGE) -> tuple[PageImage, ...]:
    """Every page of one PDF, longest side scaled to `long_edge` pixels.

    The aspect ratio is the page's own, so a landscape page is `long_edge`
    wide and a portrait page is `long_edge` tall.

    The scale is a ratio of pixels to points, not of pixels to pixels. A PDF
    page is measured in points, 72 to the inch, so US Letter is 612 by 792
    whatever it was scanned at, and a long edge of 1600 asks for about 145 dpi.
    A scale above 1 is therefore the ordinary case rather than an enlargement,
    and clamping it would render every page at 72 dpi, which is too coarse to
    read an invoice's small print.
    """
    if long_edge < 1:
        raise PageError(f"a page cannot be {long_edge} pixels on its longest side")
    try:
        document = pdfium.PdfDocument(pdf)
    except Exception as error:  # pdfium raises its own types for a bad file
        raise PageError(f"cannot open {pdf}: {error}") from error
    try:
        return tuple(
            _page(document[number], number + 1, long_edge)
            for number in range(len(document))
        )
    finally:
        document.close()


def _page(page: "pdfium.PdfPage", number: int, long_edge: int) -> PageImage:
    width, height = page.get_size()
    if width <= 0 or height <= 0:
        raise PageError(f"page {number} has no size: {width} by {height} points")
    scale = long_edge / max(width, height)
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil().convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return PageImage(
        number=number, png=buffer.getvalue(), width=image.width, height=image.height
    )


def total_size(pages: Sequence[PageImage]) -> int:
    """How many bytes the pages take together, before base64 widens them."""
    return sum(page.size for page in pages)
