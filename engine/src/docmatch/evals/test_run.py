"""Tests for scoring a run of predictions over a fixed subset.

Every case runs over the committed synthetic corpus, the same one CI runs the
eval command on, so the numbers here and the numbers in CI come from one
fixture.
"""

from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset, DocumentNotFoundError
from docmatch.evals.manifest import Manifest, load
from docmatch.evals.run import SubsetScore, read_predictions, score_subset
from docmatch.metrics.fields import PredictionError


@pytest.fixture
def run(synthetic_subset: Path) -> SubsetScore:
    return score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        read_predictions(synthetic_subset / "predictions.json"),
    )


def test_scores_every_document_the_manifest_pins(run: SubsetScore) -> None:
    assert [document.document_id for document in run.documents] == [
        "eval0004",
        "eval0002",
        "eval0006",
        "eval0003",
        "eval0005",
    ]


def test_micro_averages_the_field_score_across_the_subset(run: SubsetScore) -> None:
    """Counts are summed, not ratios averaged, so every value weighs the same."""
    assert (run.fields.true_positives, run.fields.false_negatives) == (14, 6)
    assert run.fields.false_positives == 2
    assert run.fields.precision == pytest.approx(0.875)
    assert run.fields.recall == pytest.approx(0.7)


def test_micro_averages_the_line_item_score_across_the_subset(
    run: SubsetScore,
) -> None:
    """A one-row table must not weigh as much as a thirty-row one."""
    counts = (
        run.line_items.true_positives,
        run.line_items.false_negatives,
        run.line_items.false_positives,
    )

    assert counts == (4, 3, 3)
    assert run.line_items.f1 == pytest.approx(4 / 7)


def test_reports_a_pinned_document_the_predictions_leave_out(
    run: SubsetScore,
) -> None:
    assert run.not_predicted == ("eval0006",)


def test_a_document_left_out_costs_recall_rather_than_leaving_the_subset(
    run: SubsetScore,
) -> None:
    """A backend that produced nothing for a document scores worse, not over less."""
    left_out = next(each for each in run.documents if each.document_id == "eval0006")

    assert not left_out.predicted
    assert left_out.fields.false_negatives == 4
    assert left_out.line_items.false_negatives == 1


def test_reports_a_prediction_for_a_document_the_manifest_does_not_pin(
    run: SubsetScore,
) -> None:
    """A number over the wrong documents is the failure this catches."""
    assert run.unpinned == ("eval0001",)


def test_a_document_the_manifest_does_not_pin_is_not_in_the_number(
    run: SubsetScore,
) -> None:
    assert "eval0001" not in [document.document_id for document in run.documents]


def test_breaks_the_field_score_down_by_fieldtype(run: SubsetScore) -> None:
    by_fieldtype = {each.fieldtype: each for each in run.per_fieldtype}

    assert by_fieldtype["document_id"].matched == 3
    assert by_fieldtype["document_id"].missing == 2
    assert by_fieldtype["document_id"].spurious == 1
    assert by_fieldtype["vendor_email"].matched == 0
    assert by_fieldtype["vendor_email"].spurious == 1
    assert by_fieldtype["tax_detail_rate"].matched == 2


def test_the_fieldtype_breakdown_carries_counts_and_no_values(
    run: SubsetScore,
) -> None:
    """Rule 6: the report is pasteable, so no label text is in it."""
    assert all(
        isinstance(number, int)
        for each in run.per_fieldtype
        for number in (each.matched, each.missing, each.spurious)
    )


def test_breaks_per_cell_accuracy_down_by_fieldtype(run: SubsetScore) -> None:
    by_fieldtype = {each.fieldtype: each for each in run.per_cell_fieldtype}

    assert by_fieldtype["line_item_quantity"].correct == 5
    assert by_fieldtype["line_item_quantity"].labeled == 7
    assert by_fieldtype["line_item_amount_gross"].correct == 5
    assert by_fieldtype["line_item_amount_gross"].labeled == 7
    assert by_fieldtype["line_item_amount_gross"].spurious == 2


def test_reports_a_pinned_document_the_dataset_does_not_hold(
    synthetic_subset: Path,
) -> None:
    """A manifest the dataset cannot satisfy produces no number at all."""
    manifest = Manifest(split="val", seed=1, size=1, document_ids=("eval9999",))

    with pytest.raises(DocumentNotFoundError):
        score_subset(DocileDataset(synthetic_subset), manifest, {})


def test_reads_a_predictions_file_keyed_by_document_id(
    synthetic_subset: Path,
) -> None:
    predictions = read_predictions(synthetic_subset / "predictions.json")

    assert sorted(predictions) == [
        "eval0001",
        "eval0002",
        "eval0003",
        "eval0004",
        "eval0005",
    ]
    assert predictions["eval0002"].header["vendor_name"] == ("CEDAR FREIGHT LTD",)


def test_reports_a_predictions_file_that_is_not_there(tmp_path: Path) -> None:
    absent = tmp_path / "absent.json"

    with pytest.raises(PredictionError) as raised:
        read_predictions(absent)

    assert str(absent) in str(raised.value)


def test_reports_a_predictions_file_holding_one_document_at_the_top_level(
    tmp_path: Path,
) -> None:
    """The score command's single-document file is the likely mistake here."""
    path = tmp_path / "predictions.json"
    path.write_text('{"fields": {"vendor_name": "Acme"}}', encoding="utf-8")

    with pytest.raises(PredictionError) as raised:
        read_predictions(path)

    assert "fields" in str(raised.value)


def test_names_the_document_and_the_cell_of_a_bad_prediction(tmp_path: Path) -> None:
    """Which document is wrong is the first thing to know in a file of a hundred."""
    path = tmp_path / "predictions.json"
    path.write_text(
        '{"eval0002": {"line_items": [{}, {"line_item_quantity": {"n": 2}}]}}',
        encoding="utf-8",
    )

    with pytest.raises(PredictionError) as raised:
        read_predictions(path)

    assert "eval0002.line_items.1.line_item_quantity" in str(raised.value)
