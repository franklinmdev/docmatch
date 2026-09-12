"""Tests for scoring predicted line items against the labeled ones."""

from collections.abc import Sequence

from docmatch.docile.annotation import Annotation, DocumentMetadata, LineItemCell
from docmatch.metrics.fields import FieldValues, score_fields
from docmatch.metrics.line_items import (
    CellAccuracy,
    labeled_line_items,
    score_line_items,
)

BOX = (0.0, 0.0, 1.0, 1.0)


def row(**cells: str | tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """One line item as the scorer takes it, fieldtype to every value."""
    return {
        fieldtype: (value,) if isinstance(value, str) else value
        for fieldtype, value in cells.items()
    }


def cells(
    labeled: Sequence[FieldValues], predicted: Sequence[FieldValues]
) -> dict[str, CellAccuracy]:
    """The per-cell accuracy of one scoring, by fieldtype."""
    score = score_line_items(labeled, predicted)
    return {each.fieldtype: each for each in score.per_fieldtype}


def test_rows_are_paired_to_maximize_agreement_not_by_position() -> None:
    """Which row went with which is reported, so a human can follow the pairing."""
    labeled = [
        row(line_item_description="Blue widget", line_item_quantity="2"),
        row(line_item_description="Red widget", line_item_quantity="1"),
    ]
    predicted = [
        row(line_item_description="Red widget", line_item_quantity="1"),
        row(line_item_description="Blue widget", line_item_quantity="2"),
    ]

    score = score_line_items(labeled, predicted)

    assert score.f1 == 1.0
    assert [(each.labeled, each.predicted) for each in score.rows] == [(0, 1), (1, 0)]


def test_naive_row_order_would_score_worse_than_the_assignment() -> None:
    """Two rows of the same thing, ordered the other way round.

    Position is not merely a worse pairing here, it is one that never reaches a
    correct row: each positional pair agrees on the description and disagrees
    on the quantity. The assignment swaps them and both rows are exact.
    """
    labeled = [
        row(line_item_description="Widget", line_item_quantity="1"),
        row(line_item_description="Widget", line_item_quantity="2"),
    ]
    predicted = [labeled[1], labeled[0]]

    in_order = [
        score_fields(gold, guess)
        for gold, guess in zip(labeled, predicted, strict=True)
    ]
    assigned = score_line_items(labeled, predicted)

    assert sum(each.true_positives for each in in_order) == 2
    assert not any(each.false_negatives == 0 for each in in_order)
    assert sum(each.cells.true_positives for each in assigned.rows) == 4
    assert (assigned.true_positives, assigned.false_positives) == (2, 0)


def test_reordered_rows_score_identically_to_ordered_ones() -> None:
    labeled = [
        row(line_item_description="Blue widget", line_item_amount_gross="100.00"),
        row(line_item_description="Red widget", line_item_amount_gross="136.00"),
        row(line_item_description="Green widget", line_item_amount_gross="4.00"),
    ]
    predicted = [labeled[2], labeled[0], labeled[1]]

    ordered = score_line_items(labeled, labeled)
    reordered = score_line_items(labeled, predicted)

    assert (ordered.precision, ordered.recall, ordered.f1) == (1.0, 1.0, 1.0)
    assert (reordered.precision, reordered.recall, reordered.f1) == (1.0, 1.0, 1.0)


def test_a_labeled_row_the_prediction_lacks_is_a_miss() -> None:
    labeled = [
        row(line_item_description="Blue widget"),
        row(line_item_description="Red widget"),
    ]

    score = score_line_items(labeled, [labeled[0]])

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        1,
        1,
        0,
    )
    assert (score.precision, score.recall) == (1.0, 0.5)


def test_a_predicted_row_the_label_lacks_is_a_false_positive() -> None:
    labeled = [row(line_item_description="Blue widget")]
    predicted = [*labeled, row(line_item_description="Invented widget")]

    score = score_line_items(labeled, predicted)

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        1,
        0,
        1,
    )
    assert (score.precision, score.recall) == (0.5, 1.0)


def test_a_missing_row_and_an_extra_row_in_one_table() -> None:
    """The shape a real short read has: one row dropped, one row invented."""
    labeled = [
        row(line_item_description="Blue widget", line_item_amount_gross="100.00"),
        row(line_item_description="Red widget", line_item_amount_gross="136.00"),
        row(line_item_description="Green widget", line_item_amount_gross="4.00"),
    ]
    predicted = [
        labeled[0],
        labeled[1],
        row(line_item_description="Invented widget", line_item_amount_gross="9.00"),
    ]

    score = score_line_items(labeled, predicted)

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        2,
        1,
        1,
    )
    assert (score.precision, score.recall) == (2 / 3, 2 / 3)


def test_a_row_paired_but_wrong_is_both_a_miss_and_a_false_positive() -> None:
    """A substitution: the label went unpredicted and the output carries a row
    the label does not have, so it is counted on both sides."""
    labeled = [row(line_item_description="Blue widget", line_item_quantity="2")]
    predicted = [row(line_item_description="Blue widget", line_item_quantity="3")]

    score = score_line_items(labeled, predicted)

    assert (score.true_positives, score.false_negatives, score.false_positives) == (
        0,
        1,
        1,
    )
    assert score.rows[0].predicted == 0, "it was paired; it is still wrong both ways"


def test_a_row_is_correct_only_when_every_cell_is() -> None:
    """The documented decision: all cells, not a threshold."""
    labeled = [
        row(
            line_item_description="Blue widget",
            line_item_quantity="2",
            line_item_amount_gross="100.00",
        )
    ]
    predicted = [
        row(
            line_item_description="Blue widget",
            line_item_quantity="2",
            line_item_amount_gross="110.00",
        )
    ]

    score = score_line_items(labeled, predicted)

    assert score.f1 == 0.0, "two cells out of three is not a correct row"
    assert score.rows[0].is_correct is False


def test_a_cell_the_prediction_invents_makes_the_row_wrong() -> None:
    labeled = [row(line_item_description="Blue widget")]
    predicted = [row(line_item_description="Blue widget", line_item_quantity="2")]

    score = score_line_items(labeled, predicted)

    assert score.f1 == 0.0
    assert (score.false_negatives, score.false_positives) == (1, 1)


def test_per_cell_accuracy_is_reported_per_fieldtype() -> None:
    """Partial credit lives here; the row score above has none."""
    labeled = [
        row(line_item_description="Blue widget", line_item_amount_gross="100.00"),
        row(line_item_description="Red widget", line_item_amount_gross="136.00"),
    ]
    predicted = [
        row(line_item_description="Blue widget", line_item_amount_gross="100.00"),
        row(line_item_description="Red widget", line_item_amount_gross="999.00"),
    ]

    by_fieldtype = cells(labeled, predicted)

    assert by_fieldtype["line_item_description"].correct == 2
    assert by_fieldtype["line_item_description"].accuracy == 1.0
    assert by_fieldtype["line_item_amount_gross"].correct == 1
    assert by_fieldtype["line_item_amount_gross"].labeled == 2
    assert by_fieldtype["line_item_amount_gross"].accuracy == 0.5


def test_cells_of_an_unpaired_labeled_row_count_as_incorrect() -> None:
    labeled = [row(line_item_description="Blue widget"), row(line_item_code="B-2")]

    code = cells(labeled, [labeled[0]])["line_item_code"]

    assert (code.correct, code.labeled, code.accuracy) == (0, 1, 0.0)


def test_a_fieldtype_only_ever_predicted_is_reported_as_spurious() -> None:
    labeled = [row(line_item_description="Blue widget")]
    predicted = [row(line_item_description="Blue widget", line_item_tax="1.00")]

    tax = cells(labeled, predicted)["line_item_tax"]

    assert (tax.labeled, tax.spurious) == (0, 1)


def test_a_cell_only_formatted_differently_still_matches() -> None:
    labeled = [row(line_item_amount_gross="$2,460.00", line_item_date="SEP18/26")]
    predicted = [row(line_item_amount_gross="2460", line_item_date="2026-09-18")]

    assert score_line_items(labeled, predicted).f1 == 1.0


def test_a_fieldtype_labeled_twice_in_one_row_needs_both_values() -> None:
    """A service period puts two `line_item_date` cells on the same row."""
    labeled = [row(line_item_date=("02/01/26", "02/28/26"))]

    both = score_line_items(labeled, labeled)
    one = score_line_items(labeled, [row(line_item_date="02/01/26")])

    assert both.f1 == 1.0
    assert one.f1 == 0.0, "one of the two dates is still a missing cell"


def test_two_identical_labeled_rows_both_have_to_be_predicted() -> None:
    """19% of documents repeat a line item verbatim; each one is its own row."""
    same = row(line_item_description="Blue widget", line_item_quantity="1")

    score = score_line_items([same, same], [same])

    assert (score.true_positives, score.false_negatives) == (1, 1)
    assert score.recall == 0.5


def test_a_document_with_no_line_items_on_either_side_is_vacuously_perfect() -> None:
    score = score_line_items([], [])

    assert (score.precision, score.recall, score.f1) == (1.0, 1.0, 1.0)
    assert score.rows == ()


def test_predicting_rows_for_a_document_with_none_is_all_false_positives() -> None:
    score = score_line_items([], [row(line_item_description="Invented widget")])

    assert (score.false_positives, score.precision, score.f1) == (1, 0.0, 0.0)


def test_labeled_line_items_groups_the_cells_of_each_row(
    synthetic_annotation: Annotation,
) -> None:
    rows = labeled_line_items(synthetic_annotation)

    assert rows == (
        row(
            line_item_quantity="2",
            line_item_description="Blue widget",
            line_item_amount_gross="100.00",
        ),
        row(
            line_item_quantity="1",
            line_item_units_of_measure="EA",
            line_item_description="Red widget",
            line_item_amount_gross="136.00",
        ),
    )


def test_labeled_line_items_keeps_a_fieldtype_repeated_within_a_row() -> None:
    annotation = Annotation(
        fields=[],
        cells=[
            LineItemCell(
                fieldtype="line_item_date",
                text=text,
                page=0,
                bbox=BOX,
                line_item_id=1,
            )
            for text in ("02/01/26", "02/28/26")
        ],
        metadata=DocumentMetadata(page_count=1),
    )

    assert labeled_line_items(annotation) == (
        row(line_item_date=("02/01/26", "02/28/26")),
    )
