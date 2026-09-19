"""The three records matching compares, in the shape the scorers already use.

An invoice, a purchase order and a receiving record are each a header of field
values plus lines of field values, a fieldtype to the texts under it. That is
the shape `metrics.fields` scores, so a document's labels, a backend's reading
and a generated record are one type, and the matcher cannot tell which it was
given. A receiving record's lines additionally name the purchase-order line
they were received against, as an ERP's do, so only invoice to purchase order
needs pairing (#63).

Cells
-----

A rule compares one cell per line, and a cell may be labeled under two
fieldtypes: unit price is `line_item_unit_price_gross`, else `_net`; amount is
`line_item_amount_gross`, else `_net`. Gross-else-net is the basis the
tolerance counts in #70 were taken on. Each side resolves its own cell, so a
reading that puts a gross value in the net column still has its value compared,
which matters for the OpenAI row's known net/gross split (#49).
"""

from dataclasses import dataclass
from typing import Literal

from docmatch.docile.annotation import Annotation
from docmatch.metrics.fields import FieldValues, labeled_fields
from docmatch.metrics.line_items import labeled_line_items
from docmatch.metrics.normalization import normalize_text


@dataclass(frozen=True)
class Record:
    """An invoice or a purchase order: a header and its lines, as field values.

    Lines are positions: a finding names a line by its index here, from 0.
    """

    header: FieldValues
    lines: tuple[FieldValues, ...]


@dataclass(frozen=True)
class ReceiptLine:
    """One line of a receiving record, and the purchase-order line it answers."""

    cells: FieldValues
    po_line: int


@dataclass(frozen=True)
class ReceivingRecord:
    """What was received against a purchase order: quantities, never prices."""

    lines: tuple[ReceiptLine, ...]


def labeled_record(annotation: Annotation) -> Record:
    """A document's labels as the invoice matching takes."""
    return Record(
        header=labeled_fields(annotation), lines=labeled_line_items(annotation)
    )


Cell = Literal["unit price", "amount", "quantity", "unit", "code", "description"]
"""The cells a line is compared or paired on, named apart from their fieldtypes."""

CELL_FIELDTYPES: dict[Cell, tuple[str, ...]] = {
    "unit price": ("line_item_unit_price_gross", "line_item_unit_price_net"),
    "amount": ("line_item_amount_gross", "line_item_amount_net"),
    "quantity": ("line_item_quantity",),
    "unit": ("line_item_units_of_measure",),
    "code": ("line_item_code",),
    "description": ("line_item_description",),
}
"""Each cell's fieldtypes, first one carried wins."""


@dataclass(frozen=True)
class CellValues:
    """One cell as one line carries it: the fieldtype it was found under, and
    its texts."""

    fieldtype: str
    texts: tuple[str, ...]


def cell_values(line: FieldValues, cell: Cell) -> CellValues | None:
    """The cell as this line carries it, or None when it carries no text for it.

    A text that normalizes to nothing is no text: a reading that wrote a
    blank has not carried the cell.
    """
    for fieldtype in CELL_FIELDTYPES[cell]:
        texts = tuple(text for text in line.get(fieldtype, ()) if normalize_text(text))
        if texts:
            return CellValues(fieldtype, texts)
    return None


def carries(line: FieldValues, cell: Cell) -> bool:
    return cell_values(line, cell) is not None
