"""A loop schema's settled corrections, exported for the eval (#154 point 4).

Postgres is the record: the edits table, from which the corrections are
derived at every read. `docmatch corrections` writes them to a file under the
ignored `data/corrections/`, in the shape `evals.corrections` reads, so the
eval stays free of Postgres.

A document is named in the file by its DocILE id, found from its PDF's
SHA-256 against the digests the manifest pins, the way replay and labels
recognize an upload. Only the values the reviewer changed leave, plus, when a
line was corrected, the reading's lines as left, which the eval pairs with
another reading's the way the line-item metric does.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from docmatch.evals.corrections import (
    CorrectionsError,
    Export,
    Exported,
    ExportedCell,
    ExportedDocument,
    ExportedHeader,
    ExportedLineAdded,
    ExportedLineRemoved,
    Texts,
    reading_digest,
)
from docmatch.evals.manifest import Manifest
from docmatch.evals.public import digest
from docmatch.pipeline.edits import (
    CellCorrection,
    Correction,
    Edited,
    HeaderCorrection,
    LineAddedCorrection,
)
from docmatch.pipeline.loop import Settled


def export(schema: str, settled: Sequence[Settled], pinned: Manifest) -> Export:
    """The settled documents as the eval reads them, or a message naming a
    document whose PDF the manifest does not pin."""
    by_digest = {each: document for document, each in pinned.digests.items()}
    documents = []
    for each in settled:
        document_id = by_digest.get(digest(each.invoice))
        if document_id is None:
            raise CorrectionsError(
                f"document {each.document} of schema {schema} is corrected, and "
                "its PDF is not one the manifest pins, so which DocILE document "
                "it is, and so its labels, are unknown"
            )
        documents.append(
            ExportedDocument(
                document_id=document_id,
                reading=reading_digest(each.read),
                decision=each.decision,
                corrections=tuple(_exported(c) for c in each.edited.corrections),
                lines=_lines(each.edited),
            )
        )
    return Export(database_schema=schema, documents=tuple(documents))


def _exported(correction: Correction) -> Exported:
    """A correction with the value left and never the value read, but for a
    line removed, whose cells as read are what it asserts."""
    if isinstance(correction, HeaderCorrection):
        return ExportedHeader(
            kind="header", fieldtype=correction.fieldtype, left=correction.left
        )
    if isinstance(correction, CellCorrection):
        return ExportedCell(
            kind="cell",
            line=correction.line,
            fieldtype=correction.fieldtype,
            left=correction.left,
        )
    if isinstance(correction, LineAddedCorrection):
        return ExportedLineAdded(
            kind="line added", line=correction.line, left=correction.left
        )
    return ExportedLineRemoved(
        kind="line removed", line=correction.line, read=correction.read
    )


def _lines(edited: Edited) -> dict[int, dict[str, Texts]]:
    """Every line as the edits left it, each cell with a value, when any line
    was corrected; none when only the header was."""
    if all(isinstance(each, HeaderCorrection) for each in edited.corrections):
        return {}
    return {
        line: {fieldtype: tuple(texts) for fieldtype, texts in row.items() if texts}
        for line, row in zip(edited.line_ids, edited.prediction.rows, strict=True)
    }


def write(exported: Export, out: Path) -> None:
    """The export as JSON at `out`, its directory made when missing."""
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(exported.model_dump(mode="json", by_alias=True), indent=2)
            + "\n",
            "utf-8",
        )
    except OSError as error:
        raise CorrectionsError(
            f"cannot write the corrections {out}: {error}"
        ) from error
