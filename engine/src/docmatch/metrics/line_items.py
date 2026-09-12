"""Scoring predicted line items against the labeled ones for one document.

A line item is a row of cells, and a cell is a field like any other, so one row
scored against another is `fields.score_fields`. What this module adds is the
question that has no answer at the header level: which predicted row is which
labeled row.

Assignment, not position
------------------------

Row order is not a fact about a document, it is a fact about how a backend
happened to read the table, so pairing rows by position would measure ordering
rather than extraction. Rows are paired instead by the assignment that
maximizes the total number of agreeing cells across the whole table, which is
the classic rectangular assignment problem. A pair that agrees on nothing is
not a pair; both of its rows are reported unmatched, which changes no count and
keeps the report honest.

`scipy.optimize.linear_sum_assignment` solves it, with `maximize=True` because
agreement is a profit and not a cost. It takes a rectangular matrix, so the
common case of a prediction with fewer rows than the label needs no padding:
"if it has more rows than columns, then not every row needs to be assigned to a
column, and vice versa" (scipy 1.18.1, docs read 2026-09-11). The algorithm is
a modified Jonker-Volgenant, and the matrix here is at worst 110 by 110, the
largest labeled table in the annotated set.

DocILE's own evaluation pairs line items the same way, by the matching that
maximizes total matched fields and dropping pairs that match nothing
(`docile/evaluation/line_item_matching.py` at rossumai/docile 6a7d054c3b96,
read 2026-09-11). It reaches it through `networkx`, whose
`minimum_weight_full_matching` "defers the calculation of the assignment to
SciPy" (networkx 3.6.1, docs read 2026-09-11), so the routine underneath is the
same one. What differs is the test for two cells being equal: DocILE overlaps
their boxes where this module compares their normalized text, for the reason
`normalization` gives.

When several pairings are optimal
---------------------------------

Maximizing agreeing cells does not always pick one pairing, and the tied
pairings need not agree on how many rows come out exactly right. Over 4,000
random small tables, 118 had an optimum reachable two ways, one crediting a
correct row and the other not. Leaving the choice to the solver would make the
number depend on the solver, which is the one thing a benchmark meant to stay
comparable across commits cannot afford.

So a pair is worth its agreeing cells times a scale larger than the number of
pairs, plus one more for being exact. No amount of exactness can buy a single
agreeing cell, so agreement is still maximized first; among the pairings that
maximize it, the solver takes one crediting the most correct rows. That is the
generous reading, and the right one here: the pairing is an artifact of scoring
rather than something a backend chose, so a backend should not lose a row to
it. Precision, recall and F1 are then fully determined.

Per-cell accuracy is not, quite. Two pairings equal on both counts can still
spread their agreeing cells over different fieldtypes. It is a diagnostic for
reading which column a backend is losing rather than the number the benchmark
reports, so that is where the matter is left.

When a row is correct
---------------------

A matched row counts as a true positive only when every one of its cells
matches and it carries no cell the label does not: all cells, not a threshold.
The alternative, counting a row correct above some share of its cells, would
put a number on how nearly right a row was, and a row that is nearly right is
exactly the row that reconciles to the wrong money. Partial credit still exists
and is reported, but as per-cell accuracy, where it can be read as what it is.

A row that was paired and is not exactly right is then counted twice, once as a
labeled row that went unpredicted and once as a predicted row that is not
labeled. That is what a substitution is: the label is still missing from the
output, and the output still carries a row the label does not have. It is also
how `fields` already counts a wrong value, so the two scores mean the same
thing by precision and by recall.

That strictness is why per-cell accuracy is reported per fieldtype rather than
as one number: it says which column a backend is losing, which the row score
cannot. A cell is correct when the row it belongs to was paired and the paired
row carries the same normalized value, so the cells of an unpaired labeled row
are all incorrect.

Cells within a row
------------------

A fieldtype can appear more than once in one row, most often a service period
written as two `line_item_date` cells. 10,201 of the 38,678 labeled rows repeat
a fieldtype, and 6,400 still do after normalization, so a row is a fieldtype
with a set of values rather than a fieldtype with a value, exactly as a header
is. That falls out of reusing `score_fields`.

Those two counts, and the 110 rows of the largest table, are recomputed by
`docmatch corpus`.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from scipy.optimize import linear_sum_assignment

from docmatch.docile.annotation import Annotation
from docmatch.metrics.fields import FieldScore, FieldValues, by_fieldtype, score_fields
from docmatch.metrics.score import Score, ratio


def labeled_line_items(annotation: Annotation) -> tuple[FieldValues, ...]:
    """The document's LIR labels as one mapping of cells per row, lowest id first."""
    return tuple(by_fieldtype(item.cells) for item in annotation.line_items)


def _is_exact(cells: FieldScore) -> bool:
    """Every labeled cell predicted and nothing else: one row reproduced."""
    return cells.false_negatives == 0 and cells.false_positives == 0


@dataclass(frozen=True)
class RowScore:
    """One labeled row beside the predicted row it was paired with, or neither.

    The indices are into the two sequences the scorer was given, so a human
    reading the report can find the row that went wrong.
    """

    labeled: int | None
    predicted: int | None
    cells: FieldScore

    @property
    def is_correct(self) -> bool:
        """Paired, every labeled cell predicted, and nothing else predicted."""
        return (
            self.labeled is not None
            and self.predicted is not None
            and _is_exact(self.cells)
        )


@dataclass(frozen=True)
class CellAccuracy:
    """How one LIR fieldtype went across every row of the table."""

    fieldtype: str
    correct: int
    labeled: int
    spurious: int
    """Predicted in a row whose label does not carry it."""

    @property
    def accuracy(self) -> float:
        """Of the labeled cells, how many the paired row got right."""
        return ratio(self.correct, self.labeled)


@dataclass(frozen=True)
class LineItemScore(Score):
    """One document's line-item score, per row, per fieldtype, and in aggregate."""

    rows: tuple[RowScore, ...]

    @property
    def true_positives(self) -> int:
        return sum(1 for row in self.rows if row.is_correct)

    @property
    def false_negatives(self) -> int:
        return sum(
            1 for row in self.rows if row.labeled is not None and not row.is_correct
        )

    @property
    def false_positives(self) -> int:
        return sum(
            1 for row in self.rows if row.predicted is not None and not row.is_correct
        )

    @property
    def per_fieldtype(self) -> tuple[CellAccuracy, ...]:
        """Per-cell accuracy, one entry per LIR fieldtype seen on either side."""
        correct: Counter[str] = Counter()
        labeled: Counter[str] = Counter()
        spurious: Counter[str] = Counter()
        for row in self.rows:
            for cell in row.cells.per_fieldtype:
                correct[cell.fieldtype] += len(cell.matched)
                labeled[cell.fieldtype] += len(cell.matched) + len(cell.missing)
                spurious[cell.fieldtype] += len(cell.spurious)
        return tuple(
            CellAccuracy(
                fieldtype=fieldtype,
                correct=correct[fieldtype],
                labeled=labeled[fieldtype],
                spurious=spurious[fieldtype],
            )
            for fieldtype in sorted({*correct, *labeled, *spurious})
        )


def score_line_items(
    labeled: Sequence[FieldValues], predicted: Sequence[FieldValues]
) -> LineItemScore:
    """One document's line-item score, over the pairing that agrees the most."""
    agreement = [[score_fields(gold, guess) for guess in predicted] for gold in labeled]
    paired = _pair(agreement)
    taken = set(paired.values())
    rows = [
        RowScore(
            labeled=gold,
            predicted=paired.get(gold),
            cells=agreement[gold][paired[gold]]
            if gold in paired
            else score_fields(labeled[gold], {}),
        )
        for gold in range(len(labeled))
    ]
    rows += [
        RowScore(
            labeled=None, predicted=guess, cells=score_fields({}, predicted[guess])
        )
        for guess in range(len(predicted))
        if guess not in taken
    ]
    return LineItemScore(tuple(rows))


def _pair(agreement: Sequence[Sequence[FieldScore]]) -> dict[int, int]:
    """The predicted row each labeled row is paired with, agreeing rows only.

    Both objectives go into one matrix rather than one solve each: a pair is
    worth its agreeing cells times `scale`, plus one more for being exact.
    `scale` is larger than the number of pairs, so no amount of exactness can
    buy a single agreeing cell and the solver maximizes agreement first.
    """
    if not agreement or not agreement[0]:
        return {}
    cells = [[each.true_positives for each in row] for row in agreement]
    scale = min(len(cells), len(cells[0])) + 1
    worth = [
        [
            count * scale + (1 if count > 0 and _is_exact(each) else 0)
            for count, each in zip(counts, row, strict=True)
        ]
        for counts, row in zip(cells, agreement, strict=True)
    ]
    labeled, predicted = linear_sum_assignment(worth, maximize=True)
    return {
        int(gold): int(guess)
        for gold, guess in zip(labeled, predicted, strict=True)
        if cells[gold][guess] > 0
    }
