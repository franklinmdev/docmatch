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

Receipt lines name their purchase-order line, so they need no pairing.

Rules
-----

Price variance: the invoice's unit price is above the purchase order's by
more than the tolerance in `tolerances`, on the unit price when both lines
carry one, else on the line amount (#65, #70). A cell with several texts is
compared like the gate compares listed values: the rule fires on the
combination least favourable to the buyer being clean, the highest invoice
value against the lowest purchase-order value, so a reading that disagrees
with itself cannot hide an overage.

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
from dataclasses import dataclass
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
    TAX,
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
    margin: Decimal
    """The percent of the purchase-order value, in money."""
    tolerance: Tolerance

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.type]


NotComparedReason = Literal["unreadable", "absent"]


@dataclass(frozen=True)
class NotCompared:
    """A cell a rule could not compare, and why; outside score and verdict."""

    place: Place
    cell: Cell
    text: tuple[str, ...]
    """The texts of the side that carried it, or that could not be read."""
    reason: NotComparedReason


@dataclass(frozen=True)
class MatchResult:
    """What matching says about one case."""

    pairings: tuple[Pairing, ...]
    unpaired_invoice: tuple[Unpaired, ...]
    unpaired_po: tuple[Unpaired, ...]
    findings: tuple[Finding, ...]
    not_compared: tuple[NotCompared, ...]

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
    findings: list[Finding] = []
    not_compared: list[NotCompared] = []
    for pairing in paired.pairings:
        finding = _price_variance(
            pairing,
            invoice.lines[pairing.invoice_line],
            purchase_order.lines[pairing.po_line],
            not_compared,
        )
        if finding is not None:
            findings.append(finding)
    tax = _tax_mismatch(invoice.header, purchase_order.header, not_compared)
    if tax is not None:
        findings.append(tax)
    return MatchResult(
        pairings=paired.pairings,
        unpaired_invoice=paired.unpaired_invoice,
        unpaired_po=paired.unpaired_po,
        findings=tuple(findings),
        not_compared=tuple(not_compared),
    )


def explain(finding: Finding) -> str:
    """One finding as a reader sees it, quoting the values and the constant."""
    kind = finding.place.kind.replace("po", "PO")
    place = kind if finding.place.line is None else f"{kind} {finding.place.line}"
    invoice = ", ".join(f'"{text}"' for text in finding.invoice)
    po = ", ".join(f'"{text}"' for text in finding.purchase_order)
    percent = f"{(finding.tolerance.percent * 100).normalize():f}"
    lowest = min(_numbers(finding.purchase_order))
    return (
        f"{finding.type}, {place}, {finding.cell}, invoice {invoice} vs PO {po}, "
        f"margin {finding.margin.normalize():f} ({percent} percent of {lowest:f}, "
        f"above {finding.tolerance.cent})"
    )


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


def _pair(invoice: Sequence[FieldValues], po: Sequence[FieldValues]) -> _Paired:
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
        compared = _comparable(place, cell, invoice, po, not_compared)
        if compared is None:
            continue
        invoice_cell, po_cell = compared
        highest, lowest = max(invoice_cell.numbers), min(po_cell.numbers)
        if PRICE.exceeded(highest, lowest):
            return Finding(
                type="price variance",
                place=place,
                cell=cell,
                invoice=invoice_cell.texts,
                purchase_order=po_cell.texts,
                margin=PRICE.margin(lowest),
                tolerance=PRICE,
            )
        return None
    return None


def _tax_mismatch(
    invoice: FieldValues, po: FieldValues, not_compared: list[NotCompared]
) -> Finding | None:
    """Overbilled on the header's total tax; None when clean or not comparable."""
    place = Place("header")
    compared = _comparable(place, "tax", invoice, po, not_compared)
    if compared is None:
        return None
    invoice_cell, po_cell = compared
    highest, lowest = max(invoice_cell.numbers), min(po_cell.numbers)
    if not TAX.exceeded(highest, lowest):
        return None
    return Finding(
        type="tax mismatch",
        place=place,
        cell="tax",
        invoice=invoice_cell.texts,
        purchase_order=po_cell.texts,
        margin=TAX.margin(lowest),
        tolerance=TAX,
    )


@dataclass(frozen=True)
class _Readable:
    """A cell one side carries and the normalizer read in full."""

    texts: tuple[str, ...]
    numbers: tuple[Decimal, ...]


def _comparable(
    place: Place,
    cell: Cell,
    invoice: FieldValues,
    po: FieldValues,
    not_compared: list[NotCompared],
) -> tuple[_Readable, _Readable] | None:
    """Both sides of the cell read as numbers, or None with the reason listed.

    One entry per cell and side, with one reason. Nothing is listed when
    neither side carries the cell: there is no comparison to have missed.
    When one side carries it and the other does not, the carried side is
    listed as absent, whatever the normalizer makes of it, since the
    comparison it lacks is a partner and not a number.
    """
    invoice_cell, po_cell = read_cell(invoice, cell), read_cell(po, cell)
    if invoice_cell is None and po_cell is None:
        return None
    if invoice_cell is None or po_cell is None:
        for carried in (invoice_cell, po_cell):
            if carried is not None:
                not_compared.append(NotCompared(place, cell, carried.texts, "absent"))
        return None
    readable: list[_Readable] = []
    for side in (invoice_cell, po_cell):
        if side.numbers is None:
            not_compared.append(NotCompared(place, cell, side.texts, "unreadable"))
        else:
            readable.append(_Readable(side.texts, side.numbers))
    if len(readable) < 2:
        return None
    return readable[0], readable[1]


def _numbers(texts: Sequence[str]) -> list[Decimal]:
    """The texts the normalizer reads as numbers."""
    return [value for value in map(read_number, texts) if value is not None]
