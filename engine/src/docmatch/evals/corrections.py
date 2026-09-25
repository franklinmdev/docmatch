"""Scoring a reviewer's corrections against a run, beside the fixed subset.

A correction is what a reviewer changed in a reading and left settled at the
decision (#154). `docmatch corrections` exports a loop schema's corrections to
a file under the ignored `data/corrections/`, and `docmatch eval --corrections`
reads that file, never Postgres, and asks two things of each correction: does
the scored run read what the reviewer left, and does what the reviewer left
equal the DocILE label. The second is a check on the reviewer, since every
document in this phase is labeled. Neither enters the fixed subset's F1, which
stays scored against the labels alone.

What the file holds
-------------------

Per document its DocILE id, the digest of the reading the reviewer corrected,
the decision, and each correction: a header value, a line cell, or a line added
or removed. A correction carries the value left, never the value read, since
nothing here scores the reading that was corrected. A line removed carries the
cells it was read with, and a line added the cells left on it, since those are
what they assert. A cell correction carries the fieldtype and the value left.
When any line was corrected, the file also carries every line of the reading
as left: those lines are there only to pair the reading with another the way
the line-item metric does, whole table against whole table, and nothing is
scored on a line nobody corrected (#175, and the owner's call on #176).

These shapes mirror the pipeline's corrections without the values read, and
live here so the eval imports nothing of the pipeline.

Finding a line
--------------

The line-item metric's pairing (`metrics.line_items.pair_rows`) pairs a
document's lines as left, plus the lines removed as read, against the scored
run's rows, so a line nobody corrected claims its own row and a removed line
sharing one value with it does not take that row. A cell correction is read
right when its line pairs and the paired row carries the value left; a line
added when one pairs and carries every cell left on it; a line removed when
none pairs with it. The labels are judged the same way, as one more reading.

The run the reviewer corrected
------------------------------

A correction made on the reading the scored run gives is a miss by
construction, so such a document is skipped and counted. Which reading a
reviewer corrected is known by content, its digest beside the run's, and not by
a run's name: a loop replaying run X reads exactly X's readings, while a live
loop reads its own.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from docmatch.docile.dataset import DocileDataset
from docmatch.metrics.fields import (
    FieldValues,
    Prediction,
    first_problem,
    labeled_fields,
    score_fields,
)
from docmatch.metrics.line_items import labeled_line_items, pair_rows

CORRECTIONS = Path("data/corrections")
"""Where `docmatch corrections` writes an export by default, ignored (rule 6)."""

Texts = tuple[str, ...]


class _Exported(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExportedHeader(_Exported):
    """A header fieldtype left with these values; none leaves it unread."""

    kind: Literal["header"]
    fieldtype: str
    left: Texts


class ExportedCell(_Exported):
    """A cell of a line left with these values; none leaves it unread."""

    kind: Literal["cell"]
    line: int
    fieldtype: str
    left: Texts


class ExportedLineAdded(_Exported):
    """A line the reading lacked, with the cells left on it."""

    kind: Literal["line added"]
    line: int
    left: dict[str, Texts]


class ExportedLineRemoved(_Exported):
    """A line the reading should not have had, with the cells it was read with."""

    kind: Literal["line removed"]
    line: int
    read: dict[str, Texts]


Exported = Annotated[
    ExportedHeader | ExportedCell | ExportedLineAdded | ExportedLineRemoved,
    Field(discriminator="kind"),
]


class ExportedDocument(_Exported):
    """One decided document and the corrections a reviewer left on it."""

    document_id: str
    reading: str
    """The digest of the reading as read, before any edit."""
    decision: Literal["approved", "rejected"]
    corrections: tuple[Exported, ...]
    lines: dict[int, dict[str, Texts]] = {}
    """Every line of the reading as left, by the line it is, when any line
    was corrected: only to pair the reading's lines as the metric does."""


class Export(_Exported):
    """What `docmatch corrections` writes: one schema's settled corrections."""

    model_config = ConfigDict(populate_by_name=True)

    database_schema: str = Field(alias="schema")
    documents: tuple[ExportedDocument, ...]


class CorrectionsError(Exception):
    """A corrections export cannot be read or written."""


def read_corrections(path: Path) -> Export:
    """An export, or a message saying what is wrong with it."""
    try:
        return Export.model_validate_json(path.read_bytes())
    except OSError as error:
        raise CorrectionsError(
            f"cannot read the corrections {path}: {error}"
        ) from error
    except ValidationError as error:
        raise CorrectionsError(
            f'{path} is not a corrections export: expected a "schema" and '
            '"documents", each with its "document_id", "reading", "decision" '
            f'and "corrections". {first_problem(error)}'
        ) from error


def reading_digest(prediction: Prediction) -> str:
    """The SHA-256 of a reading's values, however the file wrote them: a
    value written alone or in a list, and a field null or left out, are the
    same reading."""
    canonical = {
        "fields": _present(prediction.header),
        "line_items": [_present(row) for row in prediction.rows],
    }
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _present(values: FieldValues) -> dict[str, list[str]]:
    return {fieldtype: list(texts) for fieldtype, texts in values.items() if texts}


@dataclass(frozen=True)
class Tally:
    """Corrections of one header field, one cell's fieldtype, or one kind of
    line, and how many the run reads right and the label agrees with."""

    what: str
    corrections: int
    read_right: int
    label_agrees: int


LINE_KINDS = ("line added", "line removed")


@dataclass(frozen=True)
class CorrectionsScore:
    """An export scored against a run: tallies by header field, by cell and by
    line, and what was skipped as the run's own."""

    documents: int
    """Documents scored, the skipped ones aside."""
    header: tuple[Tally, ...]
    cells: tuple[Tally, ...]
    lines: tuple[Tally, ...]
    """Both kinds of line, always, so a zero shows."""
    skipped: int
    """Corrections made on the reading the run gives."""
    skipped_documents: int


NOTHING = Prediction()
"""What a document the run did not read is judged against."""


def score_corrections(
    export: Export, predictions: Mapping[str, Prediction], dataset: DocileDataset
) -> CorrectionsScore:
    """Each correction judged against the run's reading and the label."""
    corrections: Counter[tuple[str, str]] = Counter()
    right: Counter[tuple[str, str]] = Counter()
    agrees: Counter[tuple[str, str]] = Counter()
    skipped = skipped_documents = 0
    for document in export.documents:
        prediction = predictions.get(document.document_id, NOTHING)
        if reading_digest(prediction) == document.reading:
            skipped += len(document.corrections)
            skipped_documents += 1
            continue
        annotation = dataset.annotation(document.document_id)
        by_run = _judged(document, prediction.header, prediction.rows)
        by_label = _judged(
            document, labeled_fields(annotation), labeled_line_items(annotation)
        )
        for correction, run, label in zip(
            document.corrections, by_run, by_label, strict=True
        ):
            key = _key(correction)
            corrections[key] += 1
            right[key] += run
            agrees[key] += label

    def tallies(part: str, names: Sequence[str]) -> tuple[Tally, ...]:
        return tuple(
            Tally(
                name,
                corrections=corrections[part, name],
                read_right=right[part, name],
                label_agrees=agrees[part, name],
            )
            for name in names
        )

    def seen(part: str) -> list[str]:
        return sorted(name for each, name in corrections if each == part)

    return CorrectionsScore(
        documents=len(export.documents) - skipped_documents,
        header=tallies("header", seen("header")),
        cells=tallies("cell", seen("cell")),
        lines=tallies("line", LINE_KINDS),
        skipped=skipped,
        skipped_documents=skipped_documents,
    )


def _key(correction: Exported) -> tuple[str, str]:
    if isinstance(correction, ExportedHeader | ExportedCell):
        return correction.kind, correction.fieldtype
    return "line", correction.kind


def _judged(
    document: ExportedDocument, header: FieldValues, rows: Sequence[FieldValues]
) -> list[bool]:
    """Whether a reading, the run's or the label's, holds each correction."""
    lines: dict[int, FieldValues] = dict(document.lines)
    for correction in document.corrections:
        if isinstance(correction, ExportedLineAdded):
            lines.setdefault(correction.line, correction.left)
        elif isinstance(correction, ExportedLineRemoved):
            lines[correction.line] = correction.read
    ids = list(lines)
    paired = {
        ids[line]: rows[row]
        for line, row in pair_rows(list(lines.values()), rows).items()
    }
    judged = []
    for correction in document.corrections:
        if isinstance(correction, ExportedHeader):
            judged.append(
                _alike(
                    correction.fieldtype,
                    correction.left,
                    header.get(correction.fieldtype, ()),
                )
            )
        elif isinstance(correction, ExportedLineRemoved):
            judged.append(correction.line not in paired)
        elif (row := paired.get(correction.line)) is None:
            judged.append(False)
        elif isinstance(correction, ExportedCell):
            judged.append(
                _alike(
                    correction.fieldtype,
                    correction.left,
                    row.get(correction.fieldtype, ()),
                )
            )
        else:
            judged.append(
                all(
                    _alike(fieldtype, left, row.get(fieldtype, ()))
                    for fieldtype, left in correction.left.items()
                )
            )
    return judged


def _alike(fieldtype: str, left: Sequence[str], read: Sequence[str]) -> bool:
    """The same distinct normalized values, as the field scorer compares them."""
    score = score_fields({fieldtype: left}, {fieldtype: read})
    return score.false_negatives == 0 and score.false_positives == 0
