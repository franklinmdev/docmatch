"""Tests for the failure analysis over the committed synthetic fixture's saved
run and labels: files only, no Postgres and no model.

Two classes the fixture's run never shows, net for gross and a spurious
cell, are tested on one of its readings with one cell moved, so every class
is still decided against the fixture's own labels.
"""

from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.failures import (
    Errors,
    classify,
    classify_document,
    classify_subset,
    ranked,
    totals,
)
from docmatch.evals.manifest import load
from docmatch.evals.run import (
    DocumentScore,
    SubsetScore,
    read_predictions,
    score_subset,
)
from docmatch.metrics.fields import Prediction, labeled_fields, score_fields
from docmatch.metrics.line_items import labeled_line_items, score_line_items


@pytest.fixture
def run(synthetic_subset: Path) -> SubsetScore:
    return score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        read_predictions(synthetic_subset / "predictions.json"),
    )


def document(run: SubsetScore, document_id: str) -> Errors:
    found: DocumentScore = next(
        each for each in run.documents if each.document_id == document_id
    )
    return classify_document(found)


def reread(synthetic_subset: Path, document_id: str, reading: Prediction) -> Errors:
    """A fixture document's labels against a reading the test changed."""
    annotation = DocileDataset(synthetic_subset).annotation(document_id)
    return classify(
        score_fields(labeled_fields(annotation), reading.header),
        score_line_items(labeled_line_items(annotation), reading.rows),
    )


def saved(synthetic_subset: Path, document_id: str) -> Prediction:
    return read_predictions(synthetic_subset / "predictions.json")[document_id]


def test_a_labeled_value_left_unread_is_missing(run: SubsetScore) -> None:
    """eval0002 reads its total as null; eval0006 was not read at all."""
    assert document(run, "eval0002").header["missing", "amount_total_gross"] == 1
    assert document(run, "eval0006").header == {
        ("missing", "amount_total_gross"): 1,
        ("missing", "date_issue"): 1,
        ("missing", "document_id"): 1,
        ("missing", "vendor_name"): 1,
    }


def test_a_value_read_where_nothing_is_labeled_is_spurious(run: SubsetScore) -> None:
    """eval0002 reads an email its labels lack; eval0003 gains a derived
    currency its labels lack, and field F1 counts derived values."""
    assert document(run, "eval0002").header["spurious", "vendor_email"] == 1
    assert document(run, "eval0003").header == {
        ("spurious", "currency_code_amount_due"): 1
    }


def test_a_value_read_as_another_is_wrong_not_missing_and_spurious(
    run: SubsetScore,
) -> None:
    """eval0002's document id reads a letter O for a zero."""
    found = document(run, "eval0002").header

    assert found["wrong", "document_id"] == 1
    assert ("missing", "document_id") not in found
    assert ("spurious", "document_id") not in found


def test_a_wrong_date_is_a_misread_date(run: SubsetScore) -> None:
    """eval0004 and eval0005 both read a due date that is another day."""
    assert document(run, "eval0004").header == {("misread date", "date_due"): 1}
    assert document(run, "eval0005").header == {("misread date", "date_due"): 1}


def test_a_labeled_gross_read_as_net_is_net_for_gross(synthetic_subset: Path) -> None:
    """eval0005's saved reading with its total filed as net and a row's
    amount filed as net: both are net for gross, on the header and the cell."""
    reading = saved(synthetic_subset, "eval0005")
    fields = dict(reading.fields)
    fields["amount_total_net"] = fields.pop("amount_total_gross")
    rows = [dict(each) for each in reading.line_items]
    rows[0]["line_item_amount_net"] = rows[0].pop("line_item_amount_gross")

    found = reread(
        synthetic_subset,
        "eval0005",
        Prediction(fields=fields, line_items=tuple(rows)),
    )

    assert found.header["net for gross", "amount_total_gross"] == 1
    assert ("spurious", "amount_total_net") not in found.header
    assert found.cells["net for gross", "line_item_amount_gross"] == 1
    assert ("spurious", "line_item_amount_net") not in found.cells


def test_a_net_value_unlike_the_labeled_gross_is_not_net_for_gross(
    synthetic_subset: Path,
) -> None:
    reading = saved(synthetic_subset, "eval0005")
    fields = dict(reading.fields)
    del fields["amount_total_gross"]
    fields["amount_total_net"] = "1200.00"

    found = reread(
        synthetic_subset,
        "eval0005",
        Prediction(fields=fields, line_items=reading.line_items),
    )

    assert found.header["missing", "amount_total_gross"] == 1
    assert found.header["spurious", "amount_total_net"] == 1
    assert ("net for gross", "amount_total_gross") not in found.header


def test_a_labeled_row_left_unpaired_is_a_missing_row(run: SubsetScore) -> None:
    found = document(run, "eval0006")

    assert found.rows == {"missing row": 1}
    assert found.cells == {}


def test_a_predicted_row_left_unpaired_is_a_spurious_row(run: SubsetScore) -> None:
    """eval0005 reads a delivery row its labels do not carry."""
    assert document(run, "eval0005").rows["spurious row"] == 1


def test_a_paired_row_not_exactly_right_is_a_wrong_row(run: SubsetScore) -> None:
    assert document(run, "eval0003").rows == {"wrong row": 1}
    assert document(run, "eval0005").rows == {"wrong row": 1, "spurious row": 1}
    assert document(run, "eval0004").rows == {}


def test_a_cell_a_wrong_row_left_unread_is_a_missing_cell(run: SubsetScore) -> None:
    """eval0003 reads the torque wrench row with no quantity."""
    found = document(run, "eval0003")

    assert found.cells == {("missing", "line_item_quantity"): 1}
    assert found.one_cell_off == 1


def test_a_cell_read_as_another_value_is_a_wrong_cell(run: SubsetScore) -> None:
    """eval0005 reads one row's amount a decimal place off."""
    assert document(run, "eval0005").cells == {("wrong", "line_item_amount_gross"): 1}


def test_a_cell_a_wrong_row_carries_unlabeled_is_a_spurious_cell(
    synthetic_subset: Path,
) -> None:
    reading = saved(synthetic_subset, "eval0004")
    rows = [dict(each) for each in reading.line_items]
    rows[0]["line_item_code"] = "WD-1"

    found = reread(
        synthetic_subset,
        "eval0004",
        Prediction(fields=reading.fields, line_items=tuple(rows)),
    )

    assert found.rows == {"wrong row": 1}
    assert found.cells == {("spurious", "line_item_code"): 1}


def test_header_classes_add_back_up_to_field_f1s_counts(run: SubsetScore) -> None:
    found = classify_subset(run)

    assert totals(found.header_by_class().items()) == (
        run.fields.false_negatives,
        run.fields.false_positives,
    )
    assert found.header_by_class() == {
        "missing": 5,
        "spurious": 2,
        "wrong": 1,
        "misread date": 2,
    }


def test_row_classes_add_back_up_to_line_item_f1s_counts(run: SubsetScore) -> None:
    found = classify_subset(run)

    assert totals(found.rows.items()) == (
        run.line_items.false_negatives,
        run.line_items.false_positives,
    )
    assert found.rows == {"wrong row": 2, "missing row": 1, "spurious row": 1}
    assert found.one_cell_off == 2


def test_ranks_by_count_then_class_then_fieldtype(run: SubsetScore) -> None:
    assert ranked(classify_subset(run).header)[:3] == [
        ("misread date", "date_due", 2),
        ("missing", "amount_total_gross", 2),
        ("wrong", "document_id", 1),
    ]
