"""A regional set's labels: `labels.csv` beside its documents, keyed by filename.

A regional set is labeled by hand, by its owner, one row per document and one
column per fieldtype its manifest covers (#213, #214). The manifest pins the
file's sha256 the way it pins each document's, so a label edited after the
fact is drift and is refused rather than scored.

A cell holds the one value its document prints for that fieldtype. An empty
cell is a labeled absence, the document has no such value, so a reading of
one is a false positive; a fieldtype the manifest does not cover has no
column at all, and a reading of it counts for nothing. The `paper` column,
when there is one, says what the document was printed on and is never a
value. Line items are not labeled on a regional set yet.

Reading a set
-------------

A vision backend reads a PDF, and a paper set is images, a phone photo or a
scan of each comprobante, so each pinned image is verified against its
digest, all of them before any request, and then wrapped in a one-page PDF
for the backend to read, the way it reads a DocILE public copy. The wrapping
uses pypdfium2 5.13 (its `PdfImage.load_jpeg` and `set_bitmap`, read
2026-10-23): a JPEG is carried in as it is, never encoded a second time,
unless its EXIF turns it, which a PDF would not honour, and anything else
goes in as a bitmap pdfium compresses losslessly. The page measures the
image's pixels as points, one PDF per image in a scratch directory that is
gone when the set has been read. An e-CF set is XML, verified the same way
and handed to the backend as its own file, since only the e-CF parser reads
it (#227).

The labels file is read with the standard library's `csv` module (Python 3.12
documentation, read 2026-10-23): opened with `newline=""` as the module asks,
and as `utf-8-sig`, so a file a spreadsheet saved with a byte order mark reads
the same as one without.
"""

import csv
import io
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from docmatch.evals.manifest import Manifest
from docmatch.evals.public import digest
from docmatch.extraction.extractor import Document, Extractor
from docmatch.extraction.run import ATTEMPTS, COST_CAP, DocumentRun, read_document
from docmatch.metrics.fields import FieldValues

LABELS_FILE = "labels.csv"
FILENAME = "filename"
"""The column naming the document a row labels."""
PAPER = "paper"
"""The column saying what a document was printed on, metadata only."""


class RegionalError(Exception):
    """A regional set's files cannot be read, or are not the ones pinned."""


@dataclass(frozen=True)
class RegionalLabels:
    """The labeled values of every document a regional set pins."""

    values: Mapping[str, Mapping[str, tuple[str, ...]]]
    """Each document's covered fieldtypes to its values, by filename."""
    papers: Mapping[str, str | None]

    def header(self, document_id: str) -> FieldValues:
        return self.values[document_id]

    def rows(self, document_id: str) -> tuple[FieldValues, ...]:
        return ()

    def paper(self, document_id: str) -> str | None:
        return self.papers[document_id]


def read_labels(manifest: Manifest, directory: Path) -> RegionalLabels:
    """The labels of the set `manifest` pins, read from `directory`.

    Raises `RegionalError` naming the first thing that keeps them from being the
    set's pinned labels.
    """
    if manifest.coverage is None:
        raise RegionalError("a regional set's manifest declares no label coverage")
    pinned = manifest.files.get(LABELS_FILE)
    if pinned is None:
        raise RegionalError(f"the manifest pins no digest for {LABELS_FILE}")
    path = directory / LABELS_FILE
    try:
        content = path.read_bytes()
    except OSError as error:
        raise RegionalError(f"cannot read {path}: {error}") from error
    if digest(content) != pinned:
        raise RegionalError(f"{path} does not match the digest the manifest pins")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RegionalError(f"{path} is not UTF-8: {error}") from error
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise RegionalError(f"{path} has no header")
    header, *body = rows
    _check_columns(header, manifest.coverage, path)
    values: dict[str, dict[str, tuple[str, ...]]] = {}
    papers: dict[str, str | None] = {}
    for cells in body:
        if not cells:
            continue
        row = _row(header, cells, path)
        filename = row.pop(FILENAME)
        if filename in values:
            raise RegionalError(f"{path} labels {filename} more than once")
        paper = row.pop(PAPER, None)
        papers[filename] = (paper.strip() or None) if paper is not None else None
        values[filename] = {
            fieldtype: (text.strip(),) if text.strip() else ()
            for fieldtype, text in row.items()
        }
    _check_documents(values, manifest, path)
    return RegionalLabels(values=values, papers=papers)


def _check_columns(header: list[str], coverage: tuple[str, ...], path: Path) -> None:
    if FILENAME not in header:
        raise RegionalError(f"{path} has no {FILENAME} column")
    if len(header) != len(set(header)):
        raise RegionalError(f"{path} names a column more than once")
    unknown = [
        column for column in header if column not in (FILENAME, PAPER, *coverage)
    ]
    if unknown:
        raise RegionalError(
            f"{path} has the column {', '.join(unknown)}, which the manifest does "
            "not cover"
        )
    missing = [fieldtype for fieldtype in coverage if fieldtype not in header]
    if missing:
        raise RegionalError(
            f"the manifest covers {', '.join(missing)}, which {path} has no column for"
        )


def _row(header: list[str], cells: list[str], path: Path) -> dict[str, str]:
    if len(cells) != len(header):
        raise RegionalError(
            f"{path} has a row of {len(cells)} cells under {len(header)} columns: "
            f"{cells[0]}"
        )
    return dict(zip(header, cells, strict=True))


def _check_documents(
    values: Mapping[str, object], manifest: Manifest, path: Path
) -> None:
    pinned = set(manifest.document_ids)
    unlabeled = [each for each in manifest.document_ids if each not in values]
    if unlabeled:
        raise RegionalError(f"{path} has no row for {', '.join(unlabeled)}")
    unpinned = [each for each in values if each not in pinned]
    if unpinned:
        raise RegionalError(
            f"{path} labels {', '.join(unpinned)}, which the manifest does not pin"
        )


def is_xml(filename: str) -> bool:
    """Whether the document is an e-CF's XML, which is parsed rather than
    looked at and so is handed to the backend as it is."""
    return filename.lower().endswith(".xml")


ORIENTATION = 0x0112
"""The EXIF tag saying how a photo is turned."""


def read_set(
    extractor: Extractor,
    manifest: Manifest,
    directory: Path,
    *,
    attempts: int = ATTEMPTS,
    cost_cap: Decimal = COST_CAP,
    wait: Callable[[float], None] = time.sleep,
) -> Iterator[DocumentRun]:
    """Verify every pinned image in `directory`, then read each, yielding it as done.

    The verification happens when this is called, as `extract_subset`'s does,
    so a caller can refuse to start before it has made anything.
    """
    _verify(manifest, directory)
    return _read(
        extractor, manifest, directory, attempts=attempts, cost_cap=cost_cap, wait=wait
    )


def _verify(manifest: Manifest, directory: Path) -> None:
    if not manifest.digests:
        raise RegionalError("the manifest pins no digest for the set's images")
    for filename in manifest.document_ids:
        path = directory / filename
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RegionalError(f"cannot read {path}: {error}") from error
        if digest(content) != manifest.digests[filename]:
            raise RegionalError(
                f"{path} does not match the digest the manifest pins for {filename}"
            )


def _read(
    extractor: Extractor,
    manifest: Manifest,
    directory: Path,
    *,
    attempts: int,
    cost_cap: Decimal,
    wait: Callable[[float], None],
) -> Iterator[DocumentRun]:
    with tempfile.TemporaryDirectory(prefix="docmatch-regional-") as scratch:
        for filename in manifest.document_ids:
            path = directory / filename
            if not is_xml(filename):
                path = Path(scratch) / f"{filename}.pdf"
                _as_pdf(directory / filename, path)
            yield read_document(
                extractor,
                Document(document_id=filename, path=path, pages=1),
                attempts=attempts,
                cost_cap=cost_cap,
                wait=wait,
            )


def _as_pdf(source: Path, out: Path) -> None:
    """Write the image at `source` as a one-page PDF at `out`."""
    try:
        with Image.open(source) as image:
            carried = (
                image.format == "JPEG" and image.getexif().get(ORIENTATION, 1) == 1
            )
            upright = None if carried else ImageOps.exif_transpose(image)
            width, height = image.size if upright is None else upright.size
            pdf = pdfium.PdfDocument.new()
            page = pdf.new_page(width, height)
            picture = pdfium.PdfImage.new(pdf)
            if upright is None:
                picture.load_jpeg(source, inline=True)
            else:
                mode = "L" if upright.mode in ("1", "L") else "RGB"
                picture.set_bitmap(pdfium.PdfBitmap.from_pil(upright.convert(mode)))
    except (OSError, UnidentifiedImageError) as error:
        raise RegionalError(f"cannot read {source} as an image: {error}") from error
    picture.set_matrix(pdfium.PdfMatrix().scale(width, height))
    page.insert_obj(picture)
    page.gen_content()
    pdf.save(out)
    pdf.close()
