"""Tests for rendering a PDF's pages to images."""

import math
from pathlib import Path

import pytest

from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.pages import LONG_EDGE, PageError, render, total_size

PNG = b"\x89PNG\r\n\x1a\n"


def test_renders_one_image_per_page(tmp_path: Path) -> None:
    rendered = render(write_pdf(tmp_path / "three.pdf", pages=3))

    assert [page.number for page in rendered] == [1, 2, 3]
    assert all(page.png.startswith(PNG) for page in rendered)


def test_scales_a_portrait_page_to_the_long_edge(portrait: Path) -> None:
    """The short edge follows the page's own ratio, rounded pdfium's way.

    pdfium takes the ceiling of a scaled dimension rather than the nearest
    whole pixel, so US Letter at a long edge of 1600 is 1237 wide and not the
    1236 the ratio alone would give. Spelled out because a renderer that
    disagreed by a pixel would be a real change to what every backend is shown.
    """
    (page,) = render(portrait, long_edge=1600)

    assert page.height == 1600
    assert page.width == math.ceil(1600 / 792 * 612)


def test_scales_a_landscape_page_by_its_width(tmp_path: Path) -> None:
    wide = write_pdf(tmp_path / "wide.pdf", width=792, height=612)

    (page,) = render(wide, long_edge=1600)

    assert page.width == 1600
    assert page.height == math.ceil(1600 / 792 * 612)


def test_takes_the_orientation_from_the_page_rather_than_the_paper(
    tmp_path: Path,
) -> None:
    """A page of portrait paper, recorded rotated, renders landscape.

    12 of the 514 pages in a 400-document sample of the val split carry a
    rotation, so a renderer that ignored it would hand the model a sideways
    invoice for one page in forty.
    """
    turned = write_pdf(tmp_path / "turned.pdf", width=612, height=792, rotate=90)

    (page,) = render(turned, long_edge=1600)

    assert page.width == 1600
    assert page.width > page.height


def test_measures_the_long_edge_in_pixels_not_in_points(tmp_path: Path) -> None:
    """A one-inch page still renders at the long edge asked for.

    The page is 72 by 96 points, which is 1 by 1.33 inches. Reading the scale
    as pixels over pixels would render it 96 pixels tall, at 72 dpi, and no
    model would read the print on it.
    """
    one_inch = write_pdf(tmp_path / "small.pdf", width=72, height=96)

    (page,) = render(one_inch, long_edge=LONG_EDGE)

    assert page.height == LONG_EDGE
    assert page.width == math.ceil(LONG_EDGE / 96 * 72)


def test_refuses_a_long_edge_of_nothing(portrait: Path) -> None:
    with pytest.raises(PageError) as raised:
        render(portrait, long_edge=0)

    assert "0 pixels" in str(raised.value)


def test_names_the_file_it_could_not_open(tmp_path: Path) -> None:
    not_a_pdf = tmp_path / "invoice.pdf"
    not_a_pdf.write_bytes(b"this was never a PDF")

    with pytest.raises(PageError) as raised:
        render(not_a_pdf)

    assert "invoice.pdf" in str(raised.value)


def test_adds_up_what_the_pages_weigh(tmp_path: Path) -> None:
    rendered = render(write_pdf(tmp_path / "two.pdf", pages=2))

    assert total_size(rendered) == sum(page.size for page in rendered)
