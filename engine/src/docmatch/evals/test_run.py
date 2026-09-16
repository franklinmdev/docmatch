"""Tests for scoring a run of predictions over a fixed subset.

Every case runs over the committed synthetic corpus, the same one CI runs the
eval command on, so the numbers here and the numbers in CI come from one
fixture.
"""

from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset, DocumentNotFoundError
from docmatch.evals.confidence import Bucket, SweepLine
from docmatch.evals.manifest import Manifest, load
from docmatch.evals.run import (
    DerivedTotals,
    SubsetScore,
    read_confidence,
    read_currency_symbols,
    read_predictions,
    score_subset,
)
from docmatch.extraction.extractor import Confidence
from docmatch.gate import RULES
from docmatch.metrics.fields import Prediction, PredictionError


@pytest.fixture
def run(synthetic_subset: Path) -> SubsetScore:
    return score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        read_predictions(synthetic_subset / "predictions.json"),
        confidence=read_confidence(synthetic_subset / "confidence.json"),
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
    assert (run.fields.true_positives, run.fields.false_negatives) == (19, 8)
    assert run.fields.false_positives == 5
    assert run.fields.precision == pytest.approx(19 / 24)
    assert run.fields.recall == pytest.approx(19 / 27)


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


def test_scores_the_currency_as_read_and_with_derived_values(
    run: SubsetScore,
) -> None:
    """eval0004 labels USD and its reading only prints `$95.00`; eval0003 labels
    no currency and its reading prints `$412.50`, which code copies anyway."""
    currency = _currency(run)

    as_read = currency.as_read
    with_derived = currency.with_derived
    assert (as_read.matched, as_read.missing, as_read.spurious) == (0, 1, 0)
    assert (with_derived.matched, with_derived.missing, with_derived.spurious) == (
        1,
        0,
        1,
    )


def test_the_field_score_counts_derived_values(run: SubsetScore) -> None:
    by_fieldtype = {each.fieldtype: each for each in run.per_fieldtype}

    assert by_fieldtype["currency_code_amount_due"].matched == 1
    assert by_fieldtype["currency_code_amount_due"].spurious == 1


def test_runs_the_gate_on_every_pinned_document(run: SubsetScore) -> None:
    """eval0004 is due before it was issued and eval0003's totals are a payment
    apart; eval0005 holds both rules; eval0002's due date is unreadable and
    eval0006 was never read."""
    verdicts = {each.document_id: each.gate.verdict for each in run.documents}

    assert verdicts == {
        "eval0004": "failed",
        "eval0002": "not checked",
        "eval0006": "not checked",
        "eval0003": "failed",
        "eval0005": "passed",
    }


def test_counts_gate_verdicts_across_the_subset(run: SubsetScore) -> None:
    gate = run.gate

    assert (gate.passed, gate.failed, gate.not_checked) == (1, 2, 2)


def test_gate_pass_rate_is_passed_over_checked(run: SubsetScore) -> None:
    """Coverage stays out of the rate: not checked counts on neither side."""
    assert run.gate.checked == 3
    assert run.gate.pass_rate == pytest.approx(1 / 3)


def test_counts_unreadable_values_per_rule(run: SubsetScore) -> None:
    assert run.gate.unreadable == {"dates sane": 1, "totals agree": 0}
    assert tuple(run.gate.unreadable) == RULES


def test_the_ablation_counts_catches_misses_and_false_alarms(run: SubsetScore) -> None:
    """eval0004's due date is misread and the gate failed it: a catch. eval0005's
    due date is misread and still after its issue date: a miss. eval0003 read
    both totals right and the gate failed it anyway: a false alarm."""
    ablation = run.ablation

    assert (ablation.catches, ablation.misses, ablation.false_alarms) == (1, 1, 1)


def test_a_reading_is_wrong_only_on_values_a_checked_rule_used(
    synthetic_subset: Path,
) -> None:
    """eval0004's document id is misread too, and the gate cannot see that."""
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        {
            "eval0004": Prediction(
                fields={
                    "document_id": "INV-9999",
                    "date_issue": "July 3, 2026",
                    "date_due": "August 2, 2026",
                }
            )
        },
    )

    assert run.gate.passed == 1
    assert (run.ablation.catches, run.ablation.misses) == (0, 0)


def test_a_failed_reading_is_scored_as_is(run: SubsetScore) -> None:
    """The gate never drops a reading, so eval0003 keeps its four matched values."""
    failed = next(each for each in run.documents if each.document_id == "eval0003")

    assert failed.gate.verdict == "failed"
    assert failed.fields.true_positives == 4


def test_reports_a_pinned_document_the_dataset_does_not_hold(
    synthetic_subset: Path,
) -> None:
    """A manifest the dataset cannot satisfy produces no number at all."""
    manifest = Manifest(
        split="val", seed=1, source="synthetic", size=1, document_ids=("eval9999",)
    )

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


@pytest.mark.parametrize("amount", ["USDA 95.00", "AU$ 95.00", "95.00 CHFX"])
def test_a_currency_inside_a_longer_word_is_not_derived(
    synthetic_subset: Path, amount: str
) -> None:
    """eval0004 labels USD, so a `$` or `USD` taken from inside a word would match."""
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        {"eval0004": Prediction(fields={"amount_due": amount})},
    )

    currency = _currency(run).with_derived
    assert (currency.matched, currency.missing, currency.spurious) == (0, 1, 0)


def test_a_currency_the_scorer_does_not_know_is_not_derived(
    synthetic_subset: Path,
) -> None:
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        {"eval0004": Prediction(fields={"amount_total_gross": "95.00 CAD"})},
    )

    assert _currency(run).with_derived.spurious == 0


def test_derives_the_currency_from_symbols_the_vendor_returned(
    synthetic_subset: Path,
) -> None:
    """eval0004 labels USD, and this reading prints no currency of its own."""
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        {"eval0004": Prediction(fields={"amount_due": "95.00"})},
        currency_symbols={"eval0004": {"amount_due": ("$",)}},
    )

    currency = _currency(run)
    assert currency.as_read.matched == 0
    assert currency.with_derived.matched == 1


def test_reads_the_currency_symbols_saved_beside_the_predictions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "currency_symbols.json"
    path.write_text('{"eval0004": {"amount_due": ["$"]}}', encoding="utf-8")

    assert read_currency_symbols(path) == {"eval0004": {"amount_due": ("$",)}}


def test_a_run_with_no_currency_symbols_file_has_none(tmp_path: Path) -> None:
    """Runs saved before the file existed, and hand-written predictions."""
    assert read_currency_symbols(tmp_path / "currency_symbols.json") == {}


def test_reports_a_currency_symbols_file_that_is_not_one(tmp_path: Path) -> None:
    path = tmp_path / "currency_symbols.json"
    path.write_text('{"eval0004": "$"}', encoding="utf-8")

    with pytest.raises(PredictionError, match="currency symbols"):
        read_currency_symbols(path)


def test_calibrates_header_values_in_ten_fixed_buckets(run: SubsetScore) -> None:
    """eval0004 and eval0005 misread their due dates at 0.18 and 0.35."""
    assert run.calibration is not None
    buckets = run.calibration.header.buckets

    assert [(each.n, each.correct) for each in buckets] == [
        (0, 0),
        (1, 0),
        (0, 0),
        (1, 0),
        (0, 0),
        (0, 0),
        (1, 1),
        (1, 1),
        (2, 2),
        (10, 10),
    ]
    assert [(each.low, each.high) for each in buckets[:2]] == [(0.0, 0.1), (0.1, 0.2)]
    assert buckets[-1].high == 1.0


def test_an_empty_bucket_has_n_zero(run: SubsetScore) -> None:
    assert run.calibration is not None
    empty = run.calibration.header.buckets[0]

    assert (empty.n, empty.correct) == (0, 0)


def test_a_value_read_twice_is_counted_once_at_its_higher_confidence(
    run: SubsetScore,
) -> None:
    """eval0004 reads `$95.00` at 0.61 and `95.00` at 0.83, one normalized value."""
    assert run.calibration is not None
    buckets = run.calibration.header.buckets

    assert buckets[6] == Bucket(low=0.6, high=0.7, n=1, correct=1)
    assert buckets[8] == Bucket(low=0.8, high=0.9, n=2, correct=2)


def test_counts_values_with_no_confidence_on_their_own_line(run: SubsetScore) -> None:
    """eval0003's amount due came back with none, and eval0002 has no entry at all.

    Of eval0002's five values its document id and email are wrong.
    """
    assert run.calibration is not None

    assert run.calibration.header.no_confidence == Bucket(
        low=None, high=None, n=6, correct=4
    )


def test_derived_values_never_enter_the_calibration_table(run: SubsetScore) -> None:
    """Three readings print `$`, so code derives a currency no backend scored."""
    assert run.calibration is not None
    header = run.calibration.header
    as_read = sum(each.n for each in (*header.buckets, header.no_confidence))

    assert as_read == 22


def test_calibrates_cells_on_the_row_they_were_paired_with(run: SubsetScore) -> None:
    """eval0005's second row misreads its amount at 0.84, and its third row is
    paired with no labeled row, so its cells are wrong at 0.4 and 0.55."""
    assert run.calibration is not None
    cells = run.calibration.cells

    assert [(each.n, each.correct) for each in cells.buckets] == [
        (0, 0),
        (0, 0),
        (0, 0),
        (0, 0),
        (1, 0),
        (1, 0),
        (0, 0),
        (1, 1),
        (4, 3),
        (12, 12),
    ]
    assert (cells.no_confidence.n, cells.no_confidence.correct) == (1, 0)


def test_sweeps_the_confidence_edge_beside_the_gate(run: SubsetScore) -> None:
    """eval0004 is wrong, failed, and least confident at 0.18; eval0005 is wrong,
    passed, and least confident at 0.35; eval0003 is right, failed, and least
    confident at 0.72 on the gated values that carry one."""
    assert run.sweep is not None

    assert [
        (
            line.edge,
            line.gate_only,
            line.confidence_only,
            line.both,
            line.neither,
            line.gate_false_alarms,
            line.confidence_false_alarms,
        )
        for line in run.sweep.lines
    ] == [
        (0.1, 1, 0, 0, 1, 1, 0),
        (0.2, 0, 0, 1, 1, 1, 0),
        (0.3, 0, 0, 1, 1, 1, 0),
        (0.4, 0, 1, 1, 0, 1, 0),
        (0.5, 0, 1, 1, 0, 1, 0),
        (0.6, 0, 1, 1, 0, 1, 0),
        (0.7, 0, 1, 1, 0, 1, 0),
        (0.8, 0, 1, 1, 0, 1, 1),
        (0.9, 0, 1, 1, 0, 1, 1),
    ]


def test_counts_readings_with_a_gated_value_that_has_no_confidence(
    run: SubsetScore,
) -> None:
    """eval0003's amount due."""
    assert run.sweep is not None
    assert run.sweep.unconfident == 1


def test_a_gated_value_with_no_confidence_never_flags(synthetic_subset: Path) -> None:
    """eval0004 is due before it was issued, and neither date carries a confidence."""
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        {
            "eval0004": Prediction(
                fields={"date_issue": "July 3, 2026", "date_due": "June 2, 2026"}
            )
        },
        confidence={
            "eval0004": Confidence(fields={"date_issue": (None,), "date_due": (None,)})
        },
    )

    assert run.sweep is not None
    assert run.sweep.lines[-1] == SweepLine(
        edge=0.9,
        gate_only=1,
        confidence_only=0,
        both=0,
        neither=0,
        gate_false_alarms=0,
        confidence_false_alarms=0,
    )
    assert run.sweep.unconfident == 1


def test_a_run_with_no_confidence_has_no_signal(synthetic_subset: Path) -> None:
    run = score_subset(
        DocileDataset(synthetic_subset),
        load(synthetic_subset / "subset.json"),
        read_predictions(synthetic_subset / "predictions.json"),
    )

    assert run.calibration is None
    assert run.sweep is None


def test_reads_the_confidence_saved_beside_the_predictions(tmp_path: Path) -> None:
    path = tmp_path / "confidence.json"
    path.write_text(
        '{"eval0004": {"fields": {"date_due": [0.5, null]}, '
        '"line_items": [{"line_item_quantity": 0.25}]}}',
        encoding="utf-8",
    )

    assert read_confidence(path) == {
        "eval0004": Confidence(
            fields={"date_due": (0.5, None)},
            line_items=({"line_item_quantity": 0.25},),
        )
    }


def test_a_run_with_no_confidence_file_has_none(tmp_path: Path) -> None:
    assert read_confidence(tmp_path / "confidence.json") == {}


def test_reports_a_confidence_file_that_is_not_one(tmp_path: Path) -> None:
    path = tmp_path / "confidence.json"
    path.write_text('{"eval0004": {"fields": {"date_due": 0.5}}}', encoding="utf-8")

    with pytest.raises(PredictionError, match="confidence"):
        read_confidence(path)


def _currency(run: SubsetScore) -> DerivedTotals:
    return next(
        each for each in run.derived if each.fieldtype == "currency_code_amount_due"
    )
