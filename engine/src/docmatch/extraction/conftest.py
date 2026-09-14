"""Fixtures for the extraction tests.

No dataset and no network. Pages come from PDFs these helpers build, and the
backend is driven through the one SDK method it calls, so the whole suite runs
in CI with neither DocILE nor a key.
"""

from pathlib import Path

import pytest

HEADER = b"%PDF-1.4\n"


def write_pdf(
    path: Path, *, width: int = 612, height: int = 792, rotate: int = 0, pages: int = 1
) -> Path:
    """A valid PDF of blank pages, with a size and a rotation chosen by the caller.

    Hand-built rather than produced by a library, because what these tests are
    about is exactly the two entries a library would hide: `MediaBox`, the size
    in points, and `Rotate`, the orientation a scanner recorded. Blank pages
    render white, which is all the renderer's own tests need to measure.
    """
    kids = " ".join(f"{number + 3} 0 R" for number in range(pages))
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        f"<</Type/Pages/Kids[{kids}]/Count {pages}>>".encode("ascii"),
        *(
            (
                f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {width} {height}]"
                f"/Rotate {rotate}>>"
            ).encode("ascii")
            for _ in range(pages)
        ),
    ]
    body = bytearray(HEADER)
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj".encode("ascii") + obj + b"endobj\n"
    start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    body += b"0000000000 65535 f \n"
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode("ascii")
    body += f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\n".encode("ascii")
    body += f"startxref\n{start}\n%%EOF\n".encode("ascii")
    path.write_bytes(bytes(body))
    return path


@pytest.fixture
def portrait(tmp_path: Path) -> Path:
    """One US Letter page the right way up."""
    return write_pdf(tmp_path / "portrait.pdf")
