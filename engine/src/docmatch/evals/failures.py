"""Failure analysis: every disagreement between a reading and its labels, by
error class, in the units field F1 and line-item F1 already count.

The eval says how much a backend gets wrong; this module says in which way.
It reads the two scores the eval built for each document, the header's
`FieldScore` and the table's `LineItemScore`, and sorts each of their false
negatives and false positives into one error class, so the classes add back
up to the eval's own counts and never re-score anything (#196).

Header values
-------------

Per fieldtype the field scorer leaves the labeled values it did not find
(missing) and the values it found that are not labeled (spurious). A value
read as some other value is both at once, so on each fieldtype a missing
value and a spurious one pair off as one wrong value, and only what is left
over stays missing or spurious. A wrong value of a date fieldtype is a
misread date. Before any of that, a labeled gross amount the reading carries
under the matching net fieldtype, equal once normalized, is net for gross:
the value was read and filed in the neighbouring column. A labeled net amount
carried under the gross fieldtype is gross for net, the same mistake the
other way. A wrong value, a misread date, a net for gross or a gross for net
is one false negative and one false positive;
a missing value one false negative; a spurious value one false positive.

Rows and cells
--------------

Rows count the way line-item F1 counts them. A labeled row the pairing left
alone is a missing row, a predicted row it left alone a spurious row, and a
paired row that is not exactly right a wrong row, one false negative and one
false positive. The cells of a wrong row are then sorted the way header
values are, scope by scope, so a cell class says what made the row wrong;
the cells of a missing or spurious row belong to their row's class, not to a
cell class.

Counts, not values
------------------

Every class is a count by fieldtype. No value, description or name from a
document is kept, so the report can sit in the README under rule 6 of
`CLAUDE.md`.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from docmatch.evals.run import DocumentScore, SubsetScore
from docmatch.extraction.derived import DERIVED_FIELDTYPES
from docmatch.extraction.schema import CELL_FIELDTYPES, HEADER_FIELDTYPES
from docmatch.metrics.fields import FieldScore
from docmatch.metrics.line_items import LineItemScore
from docmatch.metrics.normalization import DATE_FIELDTYPES

ValueClass = Literal[
    "net for gross", "gross for net", "misread date", "wrong", "missing", "spurious"
]
"""How one header value or one cell of a wrong row disagrees with its label."""

VALUE_CLASSES: tuple[ValueClass, ...] = (
    "net for gross",
    "gross for net",
    "misread date",
    "wrong",
    "missing",
    "spurious",
)

RowClass = Literal["wrong row", "missing row", "spurious row"]
"""How one row disagrees with its label."""

ROW_CLASSES: tuple[RowClass, ...] = ("wrong row", "missing row", "spurious row")

NET_FOR_GROSS: tuple[tuple[str, str], ...] = (
    ("amount_total_gross", "amount_total_net"),
    ("tax_detail_gross", "tax_detail_net"),
    ("line_item_amount_gross", "line_item_amount_net"),
    ("line_item_unit_price_gross", "line_item_unit_price_net"),
)
"""Each gross fieldtype beside its net fieldtype, the two columns a backend
may file one amount under."""


KNOWN_FIELDTYPES: frozenset[str] = frozenset(
    (*HEADER_FIELDTYPES, *CELL_FIELDTYPES, *DERIVED_FIELDTYPES)
)
"""The fieldtypes the schema asks for and the one code derives. A saved
reading's keys are arbitrary strings, so only these are ever printed."""

OTHER_FIELDTYPE = "other fieldtype"
"""The one bucket every key outside the known set is counted under."""


def printable(fieldtype: str) -> str:
    """The fieldtype itself when the schema knows it, else the fixed bucket."""
    return fieldtype if fieldtype in KNOWN_FIELDTYPES else OTHER_FIELDTYPE


def false_negatives(kind: str) -> int:
    """What one error of this class takes from recall."""
    return 0 if kind in ("spurious", "spurious row") else 1


def false_positives(kind: str) -> int:
    """What one error of this class takes from precision."""
    return 0 if kind in ("missing", "missing row") else 1


@dataclass(frozen=True)
class Errors:
    """One reading's disagreements with its labels, or a whole run's, by class.

    `header` and `cells` are keyed by class and fieldtype, `rows` by class.
    `cells` covers wrong rows only, and `one_cell_off` counts the wrong rows
    whose cell classes add up to a single error. The three `_documents`
    counters give, per class, the documents with at least one error of it.
    """

    header: Counter[tuple[ValueClass, str]] = field(default_factory=Counter)
    rows: Counter[RowClass] = field(default_factory=Counter)
    cells: Counter[tuple[ValueClass, str]] = field(default_factory=Counter)
    one_cell_off: int = 0
    header_documents: Counter[ValueClass] = field(default_factory=Counter)
    row_documents: Counter[RowClass] = field(default_factory=Counter)
    cell_documents: Counter[ValueClass] = field(default_factory=Counter)

    def __add__(self, other: "Errors") -> "Errors":
        return Errors(
            header=self.header + other.header,
            rows=self.rows + other.rows,
            cells=self.cells + other.cells,
            one_cell_off=self.one_cell_off + other.one_cell_off,
            header_documents=self.header_documents + other.header_documents,
            row_documents=self.row_documents + other.row_documents,
            cell_documents=self.cell_documents + other.cell_documents,
        )

    def header_by_class(self) -> Counter[ValueClass]:
        return _by_class(self.header)

    def cells_by_class(self) -> Counter[ValueClass]:
        return _by_class(self.cells)


def _by_class(counted: Counter[tuple[ValueClass, str]]) -> Counter[ValueClass]:
    totals: Counter[ValueClass] = Counter()
    for (kind, _), count in counted.items():
        totals[kind] += count
    return totals


def _present[K](counted: Counter[K]) -> Counter[K]:
    """One document's classes that occur at all, each once."""
    return Counter({kind: 1 for kind, count in counted.items() if count > 0})


def classify(fields: FieldScore, line_items: LineItemScore) -> Errors:
    """One reading's errors, from the eval's two scores of it against its labels."""
    wrong_rows = [
        row
        for row in line_items.rows
        if row.labeled is not None and row.predicted is not None and not row.is_correct
    ]
    cells: Counter[tuple[ValueClass, str]] = Counter()
    one_cell_off = 0
    for row in wrong_rows:
        found = classify_values(row.cells)
        cells += found
        one_cell_off += sum(found.values()) == 1
    rows: Counter[RowClass] = Counter(
        {
            "wrong row": len(wrong_rows),
            "missing row": sum(1 for row in line_items.rows if row.predicted is None),
            "spurious row": sum(1 for row in line_items.rows if row.labeled is None),
        }
    )
    header = classify_values(fields)
    return Errors(
        header=header,
        rows=+rows,
        cells=cells,
        one_cell_off=one_cell_off,
        header_documents=_present(_by_class(header)),
        row_documents=_present(+rows),
        cell_documents=_present(_by_class(cells)),
    )


def classify_values(score: FieldScore) -> Counter[tuple[ValueClass, str]]:
    """One header's, or one paired row's, value errors by class and fieldtype."""
    missing = {each.fieldtype: list(each.missing) for each in score.per_fieldtype}
    spurious = {each.fieldtype: list(each.spurious) for each in score.per_fieldtype}
    found: Counter[tuple[ValueClass, str]] = Counter()
    for gross, net in NET_FOR_GROSS:
        found["net for gross", gross] += _filed_beside(missing, spurious, gross, net)
        found["gross for net", net] += _filed_beside(missing, spurious, net, gross)
    for fieldtype in sorted({*missing, *spurious}):
        unread, unlabeled = (
            len(missing.get(fieldtype, ())),
            len(spurious.get(fieldtype, ())),
        )
        swapped = min(unread, unlabeled)
        shown = printable(fieldtype)
        found[_wrong(fieldtype), shown] += swapped
        found["missing", shown] += unread - swapped
        found["spurious", shown] += unlabeled - swapped
    return +found


def _filed_beside(
    missing: dict[str, list[str]],
    spurious: dict[str, list[str]],
    labeled: str,
    read: str,
) -> int:
    """Pair off each value labeled under one fieldtype and read, equal once
    normalized, under its neighbour; how many, each taken off both lists."""
    paired = 0
    for value in list(missing.get(labeled, ())):
        if value in spurious.get(read, ()):
            missing[labeled].remove(value)
            spurious[read].remove(value)
            paired += 1
    return paired


def _wrong(fieldtype: str) -> ValueClass:
    return "misread date" if fieldtype in DATE_FIELDTYPES else "wrong"


def classify_document(document: DocumentScore) -> Errors:
    return classify(document.fields, document.line_items)


def classify_subset(run: SubsetScore) -> Errors:
    """Every pinned document's errors summed, over the scores field F1 and
    line-item F1 are drawn from."""
    return sum((classify_document(each) for each in run.documents), Errors())


def ranked(
    counted: Counter[tuple[ValueClass, str]],
) -> list[tuple[ValueClass, str, int]]:
    """Class and fieldtype by count, most first, then by class order and name."""
    order = {kind: place for place, kind in enumerate(VALUE_CLASSES)}
    return [
        (kind, fieldtype, count)
        for (kind, fieldtype), count in sorted(
            counted.items(), key=lambda item: (-item[1], order[item[0][0]], item[0][1])
        )
    ]


def totals(kinds: Iterable[tuple[str, int]]) -> tuple[int, int]:
    """False negatives and false positives over classes and their counts."""
    pairs: Sequence[tuple[str, int]] = list(kinds)
    return (
        sum(false_negatives(kind) * count for kind, count in pairs),
        sum(false_positives(kind) * count for kind, count in pairs),
    )
