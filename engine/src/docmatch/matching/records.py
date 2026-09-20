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

The header has one cell, the tax: header `amount_total_tax` only, since no
fixed-subset row labels a line tax (#65).

A rule compares one cell per line, and a cell may be labeled under two
fieldtypes: unit price is `line_item_unit_price_gross`, else `_net`; amount is
`line_item_amount_gross`, else `_net`. Gross-else-net is the basis the
tolerance counts in #70 were taken on. Each side resolves its own cell, so a
reading that puts a gross value in the net column still has its value compared,
which matters for the OpenAI row's known net/gross split (#49).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from docmatch.docile.annotation import Annotation
from docmatch.extraction.derived import derive, with_derived
from docmatch.metrics.fields import FieldValues, Prediction, labeled_fields
from docmatch.metrics.line_items import labeled_line_items
from docmatch.metrics.normalization import normalize_text, read_number


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
    """What was received against a purchase order: quantities, never prices.

    Each purchase-order line is named by one receipt line at most, so a line's
    received quantity is one receipt line's cell and never a sum the matcher
    has to decide how to take."""

    lines: tuple[ReceiptLine, ...]

    def __post_init__(self) -> None:
        named = [each.po_line for each in self.lines]
        repeated = sorted({each for each in named if named.count(each) > 1})
        if repeated:
            raise ValueError(f"receipt lines name po line {repeated[0]} more than once")

    def against(self, po_line: int) -> FieldValues | None:
        """The cells received against a purchase-order line, or None when the
        record does not cover it."""
        for each in self.lines:
            if each.po_line == po_line:
                return each.cells
        return None


def labeled_record(annotation: Annotation) -> Record:
    """A document's labels as the invoice matching takes."""
    return Record(
        header=labeled_fields(annotation), lines=labeled_line_items(annotation)
    )


def read_record(
    prediction: Prediction, currency_symbols: Mapping[str, Sequence[str]]
) -> Record:
    """A backend's reading as the invoice matching takes, with the derived
    values `eval` gives it, though no cell matching compares reads the
    derived currency (#81). Read whether it passes the gate or not: the gate
    does not filter (#67)."""
    return Record(
        header=with_derived(prediction.header, derive(prediction, currency_symbols)),
        lines=prediction.rows,
    )


LineCell = Literal["unit price", "amount", "quantity", "unit", "code", "description"]
"""The cells a line is compared or paired on, named apart from their fieldtypes."""

HeaderCell = Literal["tax"]
"""The header's own cells: the tax alone, since no fixed-subset row labels a
line tax (#65)."""

Cell = LineCell | HeaderCell
"""Every cell a rule compares, a line's or the header's."""

CELL_FIELDTYPES: dict[Cell, tuple[str, ...]] = {
    "unit price": ("line_item_unit_price_gross", "line_item_unit_price_net"),
    "amount": ("line_item_amount_gross", "line_item_amount_net"),
    "quantity": ("line_item_quantity",),
    "unit": ("line_item_units_of_measure",),
    "code": ("line_item_code",),
    "description": ("line_item_description",),
    "tax": ("amount_total_tax",),
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


def listed_units(line: FieldValues) -> frozenset[str]:
    """The units the line lists, as a unit variant compares them: after the
    text normalization, so case and spacing never make two units differ;
    empty when it lists none. The generator picks a unit outside them by the
    same rule the matcher fires on."""
    values = cell_values(line, "unit")
    if values is None:
        return frozenset()
    return frozenset(normalize_text(text) for text in values.texts)


PRICE_CELLS: tuple[LineCell, ...] = ("unit price", "amount")
"""The cells price variance falls through: the unit price where both lines
carry one the normalizer reads, else the amount (#65, #76). The generator
injects on the same cell the matcher will compare, by the same rule."""


@dataclass(frozen=True)
class ReadCell(CellValues):
    """A cell as one line carries it, with its texts read as numbers."""

    numbers: tuple[Decimal, ...] | None
    """Every text as a number, or None when the normalizer could not read one
    of them, which makes the cell unreadable rather than partly read."""


def read_cell(line: FieldValues, cell: Cell) -> ReadCell | None:
    """The cell as this line carries it, read as numbers, or None when absent."""
    values = cell_values(line, cell)
    if values is None:
        return None
    numbers = tuple(read_number(text) for text in values.texts)
    return ReadCell(
        values.fieldtype,
        values.texts,
        None if any(number is None for number in numbers) else _present(numbers),
    )


def _present(numbers: tuple[Decimal | None, ...]) -> tuple[Decimal, ...]:
    return tuple(number for number in numbers if number is not None)
