"""A reviewer's edits to a reading, and the corrections they leave (#154).

An edit changes the invoice's reading only: one header value, one line cell,
a line removed or restored, or a line added. The purchase order and the
receipt are the reference and have no edit. Every edit is saved as it is
made, append-only, and the reading as it stands is the reading as read with
every edit applied in order, so nothing is ever rewritten.

Lines keep their identity across edits. A line read by the backend is named
by its place in the reading as read; a line added is named after them, in the
order lines were added. A line removed leaves the reading as it stands and
can be restored; the reading as it stands lists its lines in the order of
their names, and `line_ids` says which line each place holds.

A correction is the net change per value: the value read against the last
value left. A value put back is none, and a value overwritten never stands.
A line removed is a correction carrying the cells it was read with; a line
added is one carrying the cells left on it, once it carries any. So a
correction asserts only what the reviewer changed (#154 point 2). The
corrections are settled at the decision because no edit is taken after it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter

from docmatch.extraction.extractor import Confidence
from docmatch.extraction.schema import CELL_FIELDTYPES, HEADER_FIELDTYPES
from docmatch.metrics.fields import Prediction, WrittenValues


def _header(fieldtype: str) -> str:
    if fieldtype not in HEADER_FIELDTYPES:
        raise ValueError(f"{fieldtype} is not a header fieldtype")
    return fieldtype


def _cell(fieldtype: str) -> str:
    if fieldtype not in CELL_FIELDTYPES:
        raise ValueError(f"{fieldtype} is not a line cell's fieldtype")
    return fieldtype


class _Edit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HeaderEdit(_Edit):
    """A header fieldtype left with one value; blank leaves it unread."""

    kind: Literal["header"]
    fieldtype: Annotated[str, AfterValidator(_header)]
    value: str


class CellEdit(_Edit):
    """A line's cell left with one value; blank leaves it unread."""

    kind: Literal["cell"]
    line: int
    fieldtype: Annotated[str, AfterValidator(_cell)]
    value: str


class LineRemoved(_Edit):
    kind: Literal["line removed"]
    line: int


class LineRestored(_Edit):
    kind: Literal["line restored"]
    line: int


class LineAdded(_Edit):
    kind: Literal["line added"]


Edit = Annotated[
    HeaderEdit | CellEdit | LineRemoved | LineRestored | LineAdded,
    Field(discriminator="kind"),
]
EDIT: TypeAdapter[Edit] = TypeAdapter(Edit)

Texts = tuple[str, ...]


class HeaderCorrection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["header"] = "header"
    fieldtype: str
    read: Texts
    left: Texts


class CellCorrection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["cell"] = "cell"
    line: int
    fieldtype: str
    read: Texts
    left: Texts


class LineRemovedCorrection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["line removed"] = "line removed"
    line: int
    read: dict[str, Texts]


class LineAddedCorrection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["line added"] = "line added"
    line: int
    left: dict[str, Texts]


Correction = (
    HeaderCorrection | CellCorrection | LineRemovedCorrection | LineAddedCorrection
)


class EditError(Exception):
    """An edit naming a line the reading does not have, or cannot take."""


@dataclass(frozen=True)
class Edited:
    """A reading with every edit applied, and what the edits leave."""

    prediction: Prediction
    confidence: Confidence | None
    line_ids: tuple[int, ...]
    """The line each place of the edited reading holds."""
    corrections: tuple[Correction, ...]


def fold(
    prediction: Prediction, confidence: Confidence | None, edits: Sequence[Edit]
) -> Edited:
    """The reading as read with `edits` applied in order. Raises `EditError`
    at the first edit that names a line not there to take it."""
    read = list(prediction.line_items)
    header: dict[str, str] = {}
    cells: dict[int, dict[str, str]] = {}
    removed: set[int] = set()
    added = 0
    for edit in edits:
        if isinstance(edit, HeaderEdit):
            header[edit.fieldtype] = edit.value.strip()
        elif isinstance(edit, LineAdded):
            added += 1
        else:
            lines = len(read) + added
            if not 0 <= edit.line < lines:
                raise EditError(f"the reading has no line {edit.line}")
            if isinstance(edit, LineRestored):
                if edit.line not in removed:
                    raise EditError(f"line {edit.line} is not removed")
                removed.discard(edit.line)
            elif edit.line in removed:
                raise EditError(f"line {edit.line} is removed")
            elif isinstance(edit, LineRemoved):
                removed.add(edit.line)
            else:
                cells.setdefault(edit.line, {})[edit.fieldtype] = edit.value.strip()

    lines = len(read) + added
    header, cells = _net(prediction, header, cells)
    ids = tuple(line for line in range(lines) if line not in removed)
    fields = _applied(prediction.fields, header)
    rows = tuple(
        _applied(read[line] if line < len(read) else {}, cells.get(line, {}))
        for line in ids
    )
    return Edited(
        Prediction(fields=fields, line_items=rows),
        _confidence(confidence, header, cells, ids),
        ids,
        _corrections(prediction, header, cells, removed, lines),
    )


def _net(
    prediction: Prediction, header: dict[str, str], cells: dict[int, dict[str, str]]
) -> tuple[dict[str, str], dict[int, dict[str, str]]]:
    """Only the values left otherwise than read: a value put back is read
    again as it was, with its confidence."""
    read_header = _texts(prediction.fields)
    read = prediction.line_items
    return (
        {
            fieldtype: value
            for fieldtype, value in header.items()
            if read_header.get(fieldtype, ()) != _left(value)
        },
        {
            line: {
                fieldtype: value
                for fieldtype, value in edited.items()
                if _texts(read[line] if line < len(read) else {}).get(fieldtype, ())
                != _left(value)
            }
            for line, edited in cells.items()
        },
    )


def _applied(written: WrittenValues, edited: dict[str, str]) -> WrittenValues:
    """Written values with each edited fieldtype left at one value, or gone
    when left blank."""
    applied = {
        fieldtype: value
        for fieldtype, value in written.items()
        if fieldtype not in edited
    }
    applied.update({fieldtype: value for fieldtype, value in edited.items() if value})
    return applied


def _confidence(
    confidence: Confidence | None,
    header: dict[str, str],
    cells: dict[int, dict[str, str]],
    ids: tuple[int, ...],
) -> Confidence | None:
    """The backend's confidence for the values it read and nobody changed: a
    value the reviewer left carries none."""
    if confidence is None:
        return None
    read = confidence.line_items
    return Confidence(
        fields={
            fieldtype: values
            for fieldtype, values in confidence.fields.items()
            if fieldtype not in header
        },
        line_items=tuple(
            {
                fieldtype: value
                for fieldtype, value in (read[line] if line < len(read) else {}).items()
                if fieldtype not in cells.get(line, {})
            }
            for line in ids
        ),
    )


def _texts(written: WrittenValues) -> dict[str, Texts]:
    """Each fieldtype read with at least one value, as its texts."""
    values = Prediction(fields=written).header
    return {fieldtype: tuple(texts) for fieldtype, texts in values.items() if texts}


def _left(value: str) -> Texts:
    return (value,) if value else ()


def _corrections(
    prediction: Prediction,
    header: dict[str, str],
    cells: dict[int, dict[str, str]],
    removed: set[int],
    lines: int,
) -> tuple[Correction, ...]:
    """The net changes, header first, then the lines by name."""
    read_header = _texts(prediction.fields)
    corrections: list[Correction] = [
        HeaderCorrection(
            fieldtype=fieldtype, read=read_header.get(fieldtype, ()), left=_left(value)
        )
        for fieldtype, value in header.items()
    ]
    read = prediction.line_items
    for line in range(lines):
        left = cells.get(line, {})
        if line >= len(read):
            if line not in removed and left:
                corrections.append(
                    LineAddedCorrection(
                        line=line,
                        left={
                            fieldtype: _left(value) for fieldtype, value in left.items()
                        },
                    )
                )
            continue
        read_cells = _texts(read[line])
        if line in removed:
            corrections.append(LineRemovedCorrection(line=line, read=read_cells))
            continue
        corrections.extend(
            CellCorrection(
                line=line,
                fieldtype=fieldtype,
                read=read_cells.get(fieldtype, ()),
                left=_left(value),
            )
            for fieldtype, value in left.items()
        )
    return tuple(corrections)
