"""The matcher: pair an invoice's lines with its purchase order's, apply the
discrepancy rules, and explain the result.

A pure function from invoice, purchase order and receiving record to a match
result, with no knowledge of how the case was built. It follows the gate's
pattern: frozen records of what was compared, and a verdict derived from
them. Numbers are read by the scorer's own normalizer and text by its text
rule, so the matcher and the metrics can never disagree about what a value
says.

Pairing
-------

Which invoice line answers which purchase-order line is one optimal one-to-one
assignment per document, solved by `scipy.optimize.linear_sum_assignment`
maximizing, as the line-item metric does. What a pair is worth is built so
that identity decides and values only break ties (#63):

- Identity is agreement on the line code or the description, each compared
  by normalized Levenshtein after the text normalization, and counting only
  at or above its cell's pairing floor (#77). Below the floor is no
  agreement, so an unrelated extra line and an added missing-line PO line do
  not pair and hide both findings.
- Values, the quantity, unit price and amount, count one each when the two
  lines agree on them. They never buy identity: a pair's worth is its identity
  in thousandths times a scale larger than every value in the table, so any
  identity difference outranks any amount of value agreement, and values only
  separate lines whose identity ties, such as lines sharing a description.
- Lines that carry neither a code nor a description, on either side, pair
  by values alone. Lines that carry one and agree below the floor do not
  pair on values: that would be pairing an unrelated line by its quantity of
  one. Nor do lines where only one side carries an identity cell: there is
  nothing to say they are the same item.
- A pair worth nothing is no pair, and both lines are reported unpaired with
  their best candidate and why it was not taken.
- A line that carries none of the cells pairing reads, a row labeled with
  only a date or a position, is not an item: it takes no part in pairing,
  is nobody's candidate, and is listed as not compared rather than reported
  unpaired, the way a rule with nothing comparable finds nothing (#76).

Every unpaired line is a finding, never dropped (#63): an unpaired invoice
line is an extra line, billed and never ordered, placed on the invoice line;
an unpaired purchase-order line is a missing line, ordered and not billed,
placed on the purchase-order line (#65, #76). Each carries its closest
candidate on the other side, that candidate's scores per cell and the reason
it was not taken, and no full score matrix.

Receipt lines name their purchase-order line, so they need no pairing: a
paired invoice line is compared with the receipt line that names its
purchase-order line.

Rules
-----

Price variance: the invoice's unit price is above the purchase order's by
more than the tolerance in `tolerances`, on the unit price when both lines
carry one, else on the line amount (#65, #70). A cell with several texts is
compared like the gate compares listed values: the rule fires on the
combination least favourable to the buyer being clean, the highest invoice
value against the lowest purchase-order value, so a reading that disagrees
with itself cannot hide an overage. Two sides that list the same numbers
agree, though: an invoice with ordered and shipped columns under one
fieldtype, copied as labeled, disagrees with itself the same way on both
sides, and hides nothing. 1,382 train and val lines list quantities that
differ, so without this every clean copy of one would be a finding.

Short-ship: the invoice's quantity is above the receipt's, so goods are billed
that were not received. Over-ship: the invoice's and the receipt's quantities
are both above the purchase order's, so more was shipped and billed than was
ordered. Quantities are compared exactly (#65, #70), listed values the same
way as prices, and on the purchase-order line, like price variance, so that
a rule suppressing both on one line reaches them in one place. A
purchase-order line the receiving record does not cover compares no
quantity: there is nothing received to be short of.

Unit variant: the invoice line's unit differs from its purchase-order line's
after the text normalization, placed on the purchase-order line (#65).
Listed units agree when both sides list the same ones, as listed values do.
There is no conversion table, so a line counted in another unit has no
price or quantity to compare: its price variance, short-ship and over-ship
are not compared, and the unit price, amount and quantity its invoice line
carries are listed as not compared with the reason `unit variant` (#66).

Tax mismatch: the invoice's header `amount_total_tax` is above the purchase
order's by more than its tolerance, the same shape as price, and is placed
on the header (#65, #70, #76). There is no line tax.

A cell one side carries and the other does not, or one the normalizer cannot
read as a number, is not compared: it is listed with its reason, the rule
falls back the way it does for an absent cell, unit price then amount, and
when nothing is comparable there is no finding (#76). The list counts in
neither the score nor the verdict, so a misread number never becomes an
eighth type or a forced hold.

The verdict
-----------

`held` when any finding is a hold, `approvable` otherwise. Never `approved`:
approving belongs to the Phase 4 state machine.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal, get_args

from scipy.optimize import linear_sum_assignment

from docmatch.matching.records import (
    PRICE_CELLS,
    Cell,
    CellValues,
    ReceivingRecord,
    Record,
    cell_values,
    read_cell,
)
from docmatch.matching.similarity import similarity
from docmatch.matching.tolerances import (
    CODE_FLOOR,
    DESCRIPTION_FLOOR,
    PRICE,
    QUANTITY,
    TAX,
    UNIT,
    Tolerance,
)
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import normalize, normalize_text, read_number

DiscrepancyType = Literal[
    "price variance",
    "short-ship",
    "over-ship",
    "extra line",
    "missing line",
    "unit variant",
    "tax mismatch",
]
TYPES: tuple[DiscrepancyType, ...] = get_args(DiscrepancyType)
"""Every discrepancy type, in the order a report lists them (#65)."""

VALUE_CELLS: tuple[Cell, ...] = ("quantity", "unit price", "amount")
"""The cells pairing counts agreement on, one each, to break identity ties."""

IDENTITY_CELLS: tuple[Cell, ...] = ("code", "description")
"""The cells identity is graded on, each against its floor."""

PAIRING_CELLS: tuple[Cell, ...] = (*IDENTITY_CELLS, *VALUE_CELLS)
"""Every cell pairing reads: identity, then the values that break its ties."""

Severity = Literal["hold", "note"]
SEVERITY: dict[DiscrepancyType, Severity] = {
    "price variance": "hold",
    "short-ship": "hold",
    "over-ship": "hold",
    "extra line": "hold",
    "missing line": "note",
    "unit variant": "hold",
    "tax mismatch": "hold",
}
"""Fixed per type: every type holds except missing line, since partial
invoicing is normal (#65)."""

Verdict = Literal["held", "approvable"]


@dataclass(frozen=True)
class Place:
    """Where a finding is: a purchase-order line, an invoice line, or the header.

    The purchase-order line for price variance, short-ship, over-ship, unit
    variant and missing line; the header for tax mismatch; the invoice line
    for extra line only, so that a reading that drops a row shifts no other
    finding's place (#76). Lines are indexed from 0 in their record.
    """

    kind: Literal["po line", "invoice line", "header"]
    line: int | None = None


@dataclass(frozen=True)
class Pairing:
    """One invoice line and the purchase-order line it answers, with the scores
    that paired them; a score is None when either side lacks the cell."""

    invoice_line: int
    po_line: int
    code: float | None
    description: float | None


Reason = Literal["below the floor", "taken by another line", "no agreement"]
"""Why an unpaired line's best candidate was not taken: its identity agreed
below the floor, another line took it, or the two had no identity cell in
common, so only values could have paired them and did not."""


@dataclass(frozen=True)
class Candidate:
    """The closest line on the other side, its scores, and why it was not taken."""

    line: int
    code: float | None
    description: float | None
    reason: Reason


@dataclass(frozen=True)
class Unpaired:
    """A line pairing left without a partner, never dropped (#63)."""

    line: int
    candidate: Candidate | None
    """None when the other record has no lines at all."""


@dataclass(frozen=True)
class Finding:
    """A discrepancy type at a place, with everything its explanation quotes."""

    type: DiscrepancyType
    place: Place
    cell: Cell
    invoice: tuple[str, ...]
    """The invoice's texts for the cell, exactly as it has them."""
    purchase_order: tuple[str, ...]
    """Empty when the rule does not compare against the purchase order, as
    short-ship does not."""
    margin: Decimal
    """The percent of the value compared against, in money: the purchase
    order's, or the receipt's for a short-ship; 0 when exact."""
    tolerance: Tolerance
    receipt: tuple[str, ...] = ()
    """The receipt's texts for the cell, for the rules that compare it."""

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.type]


NotComparedReason = Literal[
    "unreadable", "absent", "unit variant", "nothing to pair on"
]


UnpairedType = Literal["extra line", "missing line"]


@dataclass(frozen=True)
class UnpairedFinding:
    """A line pairing left without a partner, as a finding: an extra line on
    the invoice line, a missing line on the purchase-order line."""

    type: UnpairedType
    place: Place
    candidate: Candidate | None
    """The closest line on the other side, or None when it has no lines."""

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.type]


@dataclass(frozen=True)
class NotCompared:
    """A cell a rule could not compare, and why; outside score and verdict."""

    place: Place
    cell: Cell | None
    """None when the whole line was not compared, having nothing to pair on."""
    text: tuple[str, ...]
    """The texts of the side that carried it, or that could not be read; for
    a unit variant, what the invoice billed."""
    reason: NotComparedReason


@dataclass(frozen=True)
class MatchResult:
    """What matching says about one case."""

    pairings: tuple[Pairing, ...]
    findings: tuple[Finding | UnpairedFinding, ...]
    """Value findings on paired lines, the header's tax, then extra lines,
    then missing lines."""
    not_compared: tuple[NotCompared, ...]

    @property
    def unpaired_invoice(self) -> tuple[Unpaired, ...]:
        """The invoice lines pairing left without a partner."""
        return self._unpaired("extra line")

    @property
    def unpaired_po(self) -> tuple[Unpaired, ...]:
        """The purchase-order lines pairing left without a partner."""
        return self._unpaired("missing line")

    def _unpaired(self, type_: UnpairedType) -> tuple[Unpaired, ...]:
        return tuple(
            Unpaired(each.place.line, each.candidate)
            for each in self.findings
            if isinstance(each, UnpairedFinding)
            and each.type == type_
            and each.place.line is not None
        )

    @property
    def verdict(self) -> Verdict:
        if any(each.severity == "hold" for each in self.findings):
            return "held"
        return "approvable"


def match(
    invoice: Record, purchase_order: Record, receipt: ReceivingRecord
) -> MatchResult:
    """One case matched: pairings, findings, what was not compared, verdict."""
    paired = _pair(invoice.lines, purchase_order.lines)
    findings: list[Finding | UnpairedFinding] = []
    not_compared = [
        *_nothing_to_pair_on("invoice line", invoice.lines),
        *_nothing_to_pair_on("po line", purchase_order.lines),
    ]
    for pairing in paired.pairings:
        invoice_line = invoice.lines[pairing.invoice_line]
        po_line = purchase_order.lines[pairing.po_line]
        unit = _unit_variant(pairing, invoice_line, po_line, not_compared)
        if unit is not None:
            findings.append(unit)
            not_compared += _suppressed(unit.place, invoice_line)
            continue
        finding = _price_variance(pairing, invoice_line, po_line, not_compared)
        if finding is not None:
            findings.append(finding)
        findings += _quantity_findings(
            pairing,
            invoice_line,
            po_line,
            receipt.against(pairing.po_line),
            not_compared,
        )
    tax = _tax_mismatch(invoice.header, purchase_order.header, not_compared)
    if tax is not None:
        findings.append(tax)
    findings += [
        UnpairedFinding("extra line", Place("invoice line", each.line), each.candidate)
        for each in paired.unpaired_invoice
    ]
    findings += [
        UnpairedFinding("missing line", Place("po line", each.line), each.candidate)
        for each in paired.unpaired_po
    ]
    return MatchResult(
        pairings=paired.pairings,
        findings=tuple(findings),
        not_compared=tuple(not_compared),
    )


def explain(finding: Finding | UnpairedFinding) -> str:
    """One finding as a reader sees it: the values and the constant it
    applied, or for an unpaired line, its closest candidate and why not."""
    place = _place(finding.place)
    if isinstance(finding, UnpairedFinding):
        return f"{finding.type}, {place}, {_why_unpaired(finding)}"
    billed = [f"invoice {_quoted(finding.invoice)}"]
    if finding.purchase_order:
        if finding.receipt:
            billed.append(f"receipt {_quoted(finding.receipt)}")
        against = f"PO {_quoted(finding.purchase_order)}"
    else:
        against = f"receipt {_quoted(finding.receipt)}"
    compared = (
        f"{finding.type}, {place}, {finding.cell}, {' and '.join(billed)} vs {against}"
    )
    if finding.tolerance.exact:
        return f"{compared}, compared exactly"
    percent = f"{(finding.tolerance.percent * 100).normalize():f}"
    lowest = min(_numbers(finding.purchase_order))
    return (
        f"{compared}, margin {finding.margin.normalize():f} "
        f"({percent} percent of {lowest:f}, above {finding.tolerance.cent})"
    )


def _quoted(texts: Sequence[str]) -> str:
    return ", ".join(f'"{text}"' for text in texts)


def _place(place: Place) -> str:
    kind = place.kind.replace("po", "PO")
    return kind if place.line is None else f"{kind} {place.line}"


def _why_unpaired(finding: UnpairedFinding) -> str:
    other = "PO" if finding.type == "extra line" else "invoice"
    candidate = finding.candidate
    if candidate is None:
        return f"the {other} has no lines"
    scores = ", ".join(
        f"{cell}: no {cell}" if score is None else f"{cell} {score:.3f}"
        for cell, score in (
            ("code", candidate.code),
            ("description", candidate.description),
        )
    )
    return f"closest {other} line {candidate.line}, {scores}, {candidate.reason}"


# Pairing


@dataclass(frozen=True)
class _Comparison:
    """One invoice line against one purchase-order line."""

    code: float | None
    description: float | None
    values: int
    """How many of quantity, unit price and amount the two agree on."""
    nameless: bool
    """Whether neither line carries a code or a description, the one case
    values pair on their own (#63)."""

    @property
    def comparable(self) -> bool:
        """Whether the two have an identity cell in common."""
        return self.code is not None or self.description is not None

    @property
    def identity(self) -> int:
        """Agreement at or above the floors, in thousandths, summed over cells."""
        agreed = 0
        if self.code is not None and self.code >= CODE_FLOOR:
            agreed += round(self.code * 1000)
        if self.description is not None and self.description >= DESCRIPTION_FLOOR:
            agreed += round(self.description * 1000)
        return agreed

    @property
    def closeness(self) -> tuple[float, int]:
        """What a best candidate is picked by: similarity with the floors
        ignored, then values."""
        return ((self.code or 0.0) + (self.description or 0.0), self.values)

    def worth(self, scale: int) -> int:
        if self.identity:
            return self.identity * scale + self.values
        return self.values if self.nameless else 0


@dataclass(frozen=True)
class _Paired:
    pairings: tuple[Pairing, ...]
    unpaired_invoice: tuple[Unpaired, ...]
    unpaired_po: tuple[Unpaired, ...]


def _nothing_to_pair_on(
    kind: Literal["invoice line", "po line"], lines: Sequence[FieldValues]
) -> list[NotCompared]:
    """The lines pairing leaves out, as not compared."""
    return [
        NotCompared(Place(kind, position), None, (), "nothing to pair on")
        for position, line in enumerate(lines)
        if not pairable(line)
    ]


def pairable(line: FieldValues) -> bool:
    """Whether the line carries any cell pairing reads; one that does not is
    left out of pairing."""
    return any(cell_values(line, cell) is not None for cell in PAIRING_CELLS)


def _pair(invoice: Sequence[FieldValues], po: Sequence[FieldValues]) -> _Paired:
    """Pair the lines that carry a pairing cell, and name them by their
    position in the whole record."""
    rows = [position for position, line in enumerate(invoice) if pairable(line)]
    columns = [position for position, line in enumerate(po) if pairable(line)]
    paired = _pair_items(
        [invoice[each] for each in rows], [po[each] for each in columns]
    )
    return _Paired(
        tuple(
            replace(
                each,
                invoice_line=rows[each.invoice_line],
                po_line=columns[each.po_line],
            )
            for each in paired.pairings
        ),
        tuple(_renamed(each, rows, columns) for each in paired.unpaired_invoice),
        tuple(_renamed(each, columns, rows) for each in paired.unpaired_po),
    )


def _renamed(unpaired: Unpaired, own: list[int], other: list[int]) -> Unpaired:
    """An unpaired line and its candidate named by their positions in the
    whole records, `own` and `other` mapping the pairable lines back."""
    candidate = unpaired.candidate
    return Unpaired(
        own[unpaired.line],
        None if candidate is None else replace(candidate, line=other[candidate.line]),
    )


def _pair_items(invoice: Sequence[FieldValues], po: Sequence[FieldValues]) -> _Paired:
    compared = [[_compare(one, other) for other in po] for one in invoice]
    # Larger than every value in the table, so no amount of value agreement
    # buys a thousandth of identity.
    scale = len(VALUE_CELLS) * min(len(invoice), len(po)) + 1
    assigned: dict[int, int] = {}
    if invoice and po:
        worth = [[each.worth(scale) for each in row] for row in compared]
        rows, columns = linear_sum_assignment(worth, maximize=True)
        assigned = {
            int(row): int(column)
            for row, column in zip(rows, columns, strict=True)
            if worth[row][column] > 0
        }
    taken = {column: row for row, column in assigned.items()}
    pairings = tuple(
        Pairing(
            invoice_line=row,
            po_line=column,
            code=compared[row][column].code,
            description=compared[row][column].description,
        )
        for row, column in sorted(assigned.items())
    )
    unpaired_invoice = tuple(
        Unpaired(row, _candidate(compared[row], taken, scale))
        for row in range(len(invoice))
        if row not in assigned
    )
    unpaired_po = tuple(
        Unpaired(column, _candidate([row[column] for row in compared], assigned, scale))
        for column in range(len(po))
        if column not in taken
    )
    return _Paired(pairings, unpaired_invoice, unpaired_po)


def _candidate(
    against: Sequence[_Comparison], taken: dict[int, int], scale: int
) -> Candidate | None:
    """The closest line on the other side, and why it was not taken."""
    if not against:
        return None
    best = max(range(len(against)), key=lambda each: against[each].closeness)
    comparison = against[best]
    if best in taken and comparison.worth(scale) > 0:
        reason: Reason = "taken by another line"
    elif comparison.comparable:
        reason = "below the floor"
    else:
        reason = "no agreement"
    return Candidate(best, comparison.code, comparison.description, reason)


def _compare(one: FieldValues, other: FieldValues) -> _Comparison:
    codes = (cell_values(one, "code"), cell_values(other, "code"))
    descriptions = (cell_values(one, "description"), cell_values(other, "description"))
    return _Comparison(
        code=_alike(*codes),
        description=_alike(*descriptions),
        values=sum(_agree(one, other, cell) for cell in VALUE_CELLS),
        nameless=all(each is None for each in (*codes, *descriptions)),
    )


def _alike(one: CellValues | None, other: CellValues | None) -> float | None:
    """The closest two texts of the cell come, or None when a side lacks it."""
    if one is None or other is None:
        return None
    return max(
        similarity(normalize_text(left), normalize_text(right))
        for left in one.texts
        for right in other.texts
    )


def _agree(one: FieldValues, other: FieldValues, cell: Cell) -> bool:
    """Whether both carry the cell and share a normalized value."""
    left, right = cell_values(one, cell), cell_values(other, cell)
    if left is None or right is None:
        return False
    return bool(
        {normalize(left.fieldtype, text) for text in left.texts}
        & {normalize(right.fieldtype, text) for text in right.texts}
    )


# Rules


def _unit_variant(
    pairing: Pairing,
    invoice: FieldValues,
    po: FieldValues,
    not_compared: list[NotCompared],
) -> Finding | None:
    """The invoice line counted in a unit the purchase-order line is not; None
    when the two agree, or when a side carries no unit, listing the other's."""
    place = Place("po line", pairing.po_line)
    billed, ordered = cell_values(invoice, "unit"), cell_values(po, "unit")
    if billed is None or ordered is None:
        for carried in (billed, ordered):
            if carried is not None:
                not_compared.append(NotCompared(place, "unit", carried.texts, "absent"))
        return None
    if {normalize_text(text) for text in billed.texts} == {
        normalize_text(text) for text in ordered.texts
    }:
        return None
    return Finding(
        type="unit variant",
        place=place,
        cell="unit",
        invoice=billed.texts,
        purchase_order=ordered.texts,
        margin=Decimal(0),
        tolerance=UNIT,
    )


SUPPRESSED_CELLS: tuple[Cell, ...] = (*PRICE_CELLS, "quantity")
"""What a unit variant leaves uncompared on its line: a price or a quantity
counted in another unit is not comparable without a conversion table (#66)."""


def _suppressed(place: Place, invoice: FieldValues) -> list[NotCompared]:
    """Each suppressed cell the invoice line carries, with the texts it billed."""
    return [
        NotCompared(place, cell, values.texts, "unit variant")
        for cell in SUPPRESSED_CELLS
        if (values := cell_values(invoice, cell)) is not None
    ]


def _price_variance(
    pairing: Pairing,
    invoice: FieldValues,
    po: FieldValues,
    not_compared: list[NotCompared],
) -> Finding | None:
    """Overbilled on the unit price, else on the amount; None when clean or
    when nothing is comparable."""
    place = Place("po line", pairing.po_line)
    for cell in PRICE_CELLS:
        compared = _comparable(place, cell, (invoice, po), not_compared)
        if compared is not None:
            return _overbilled("price variance", place, cell, PRICE, *compared)
    return None


def _tax_mismatch(
    invoice: FieldValues, po: FieldValues, not_compared: list[NotCompared]
) -> Finding | None:
    """Overbilled on the header's total tax; None when clean or not comparable."""
    place = Place("header")
    compared = _comparable(place, "tax", (invoice, po), not_compared)
    if compared is None:
        return None
    return _overbilled("tax mismatch", place, "tax", TAX, *compared)


def _overbilled(
    type_: DiscrepancyType,
    place: Place,
    cell: Cell,
    tolerance: Tolerance,
    invoice: "_Readable",
    po: "_Readable",
) -> Finding | None:
    """A finding when the invoice's value is above the purchase order's by more
    than the tolerance, else None."""
    if not _above(invoice, po, tolerance):
        return None
    lowest = min(po.numbers)
    return Finding(
        type=type_,
        place=place,
        cell=cell,
        invoice=invoice.texts,
        purchase_order=po.texts,
        margin=tolerance.margin(lowest),
        tolerance=tolerance,
    )


def _quantity_findings(
    pairing: Pairing,
    invoice: FieldValues,
    po: FieldValues,
    received: FieldValues | None,
    not_compared: list[NotCompared],
) -> list[Finding]:
    """Short-ship against the receipt and over-ship against the purchase
    order, compared exactly; none when the receiving record does not cover the
    line. A short-ship needs no purchase-order quantity, so one that is
    absent or unreadable leaves only the over-ship not compared."""
    if received is None:
        return []
    place = Place("po line", pairing.po_line)
    compared = _comparable(place, "quantity", (invoice, received), not_compared)
    if compared is None:
        _po_quantity_not_compared(place, invoice, po, received, not_compared)
        return []
    invoice_cell, receipt_cell = compared

    def found(type_: DiscrepancyType, against: _Readable) -> Finding:
        return Finding(
            type=type_,
            place=place,
            cell="quantity",
            invoice=invoice_cell.texts,
            purchase_order=() if against is receipt_cell else against.texts,
            receipt=receipt_cell.texts,
            margin=QUANTITY.margin(min(against.numbers)),
            tolerance=QUANTITY,
        )

    findings: list[Finding] = []
    if _above(invoice_cell, receipt_cell, QUANTITY):
        findings.append(found("short-ship", receipt_cell))
    po_cell = read_cell(po, "quantity")
    if po_cell is None:
        for carried in (invoice_cell, receipt_cell):
            not_compared.append(NotCompared(place, "quantity", carried.texts, "absent"))
    elif po_cell.numbers is None:
        not_compared.append(NotCompared(place, "quantity", po_cell.texts, "unreadable"))
    else:
        ordered = _Readable(po_cell.texts, po_cell.numbers)
        if _above(invoice_cell, ordered, QUANTITY) and _above(
            receipt_cell, ordered, QUANTITY
        ):
            findings.append(found("over-ship", ordered))
    return findings


def _po_quantity_not_compared(
    place: Place,
    invoice: FieldValues,
    po: FieldValues,
    received: FieldValues,
    not_compared: list[NotCompared],
) -> None:
    """List the purchase order's quantity when the over-ship could not reach
    it, the way `_comparable` lists a side: absent when the invoice or the
    receipt lacks one, unreadable when only its own text is at fault."""
    po_cell = read_cell(po, "quantity")
    if po_cell is None:
        return
    partners = (read_cell(invoice, "quantity"), read_cell(received, "quantity"))
    if None in partners:
        not_compared.append(NotCompared(place, "quantity", po_cell.texts, "absent"))
    elif po_cell.numbers is None:
        not_compared.append(NotCompared(place, "quantity", po_cell.texts, "unreadable"))


@dataclass(frozen=True)
class _Readable:
    """A cell one side carries and the normalizer read in full."""

    texts: tuple[str, ...]
    numbers: tuple[Decimal, ...]


def _above(side: _Readable, against: _Readable, tolerance: Tolerance) -> bool:
    """Whether the side's highest value exceeds the other's lowest, unless the
    two list the same numbers, which is agreement."""
    if set(side.numbers) == set(against.numbers):
        return False
    return tolerance.exceeded(max(side.numbers), min(against.numbers))


def _comparable(
    place: Place,
    cell: Cell,
    sides: Sequence[FieldValues],
    not_compared: list[NotCompared],
) -> tuple[_Readable, ...] | None:
    """Every side's cell read as numbers, in the order given, or None with
    the reason listed.

    One entry per cell and side, with one reason. Nothing is listed when no
    side carries the cell: there is no comparison to have missed. When some
    sides carry it and another does not, each carried side is listed as
    absent, whatever the normalizer makes of it, since the comparison it
    lacks is a partner and not a number.
    """
    cells = [read_cell(side, cell) for side in sides]
    carried = [each for each in cells if each is not None]
    if not carried:
        return None
    if len(carried) < len(cells):
        for each in carried:
            not_compared.append(NotCompared(place, cell, each.texts, "absent"))
        return None
    readable: list[_Readable] = []
    for each in carried:
        if each.numbers is None:
            not_compared.append(NotCompared(place, cell, each.texts, "unreadable"))
        else:
            readable.append(_Readable(each.texts, each.numbers))
    if len(readable) < len(cells):
        return None
    return tuple(readable)


def _numbers(texts: Sequence[str]) -> list[Decimal]:
    """The texts the normalizer reads as numbers."""
    return [value for value in map(read_number, texts) if value is not None]
