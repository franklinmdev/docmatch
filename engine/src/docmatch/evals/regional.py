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

The file is read with the standard library's `csv` module (Python 3.12
documentation, read 2026-10-23): opened with `newline=""` as the module asks,
and as `utf-8-sig`, so a file a spreadsheet saved with a byte order mark reads
the same as one without.
"""

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from docmatch.evals.manifest import Manifest
from docmatch.evals.public import digest
from docmatch.metrics.fields import FieldValues

LABELS_FILE = "labels.csv"
FILENAME = "filename"
"""The column naming the document a row labels."""
PAPER = "paper"
"""The column saying what a document was printed on, metadata only."""


class LabelsError(Exception):
    """A regional set's labels cannot be read, or are not the ones pinned."""


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

    Raises `LabelsError` naming the first thing that keeps them from being the
    set's pinned labels.
    """
    if manifest.coverage is None:
        raise LabelsError("a regional set's manifest declares no label coverage")
    pinned = manifest.files.get(LABELS_FILE)
    if pinned is None:
        raise LabelsError(f"the manifest pins no digest for {LABELS_FILE}")
    path = directory / LABELS_FILE
    try:
        content = path.read_bytes()
    except OSError as error:
        raise LabelsError(f"cannot read {path}: {error}") from error
    if digest(content) != pinned:
        raise LabelsError(f"{path} does not match the digest the manifest pins")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise LabelsError(f"{path} is not UTF-8: {error}") from error
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise LabelsError(f"{path} has no header")
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
            raise LabelsError(f"{path} labels {filename} more than once")
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
        raise LabelsError(f"{path} has no {FILENAME} column")
    if len(header) != len(set(header)):
        raise LabelsError(f"{path} names a column more than once")
    unknown = [
        column for column in header if column not in (FILENAME, PAPER, *coverage)
    ]
    if unknown:
        raise LabelsError(
            f"{path} has the column {', '.join(unknown)}, which the manifest does "
            "not cover"
        )
    missing = [fieldtype for fieldtype in coverage if fieldtype not in header]
    if missing:
        raise LabelsError(
            f"the manifest covers {', '.join(missing)}, which {path} has no column for"
        )


def _row(header: list[str], cells: list[str], path: Path) -> dict[str, str]:
    if len(cells) != len(header):
        raise LabelsError(
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
        raise LabelsError(f"{path} has no row for {', '.join(unlabeled)}")
    unpinned = [each for each in values if each not in pinned]
    if unpinned:
        raise LabelsError(
            f"{path} labels {', '.join(unpinned)}, which the manifest does not pin"
        )
