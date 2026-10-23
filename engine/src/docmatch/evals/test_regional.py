"""Tests for reading a regional set's labels, on sets written by each test."""

from pathlib import Path

import pytest

from docmatch.evals.manifest import Manifest
from docmatch.evals.public import digest
from docmatch.evals.regional import LabelsError, read_labels
from docmatch.evals.run import SubsetScore, score_subset
from docmatch.extraction.extractor import Confidence
from docmatch.metrics.fields import Prediction

COVERAGE = ("vendor_tax_id", "document_id", "amount_total_gross")

LABELS = (
    "filename,vendor_tax_id,document_id,amount_total_gross,paper\n"
    "do0001.png,130000001,B0100000001,1180.00,thermal\n"
    "do0002.png,130000002,,590.00,letter\n"
)


def a_set(directory: Path, labels: str = LABELS, **update: object) -> Manifest:
    """Write `labels` beside a manifest pinning two documents and its digest."""
    (directory / "labels.csv").write_text(labels, encoding="utf-8")
    return Manifest(
        split="paper",
        seed=0,
        source="synthetic",
        size=2,
        document_ids=("do0001.png", "do0002.png"),
        region="do",
        coverage=COVERAGE,
        files={"labels.csv": digest(labels.encode())},
    ).model_copy(update=update)


def test_reads_each_covered_value_by_filename(tmp_path: Path) -> None:
    labels = read_labels(a_set(tmp_path), tmp_path)

    assert labels.header("do0001.png") == {
        "vendor_tax_id": ("130000001",),
        "document_id": ("B0100000001",),
        "amount_total_gross": ("1180.00",),
    }


def test_an_empty_cell_is_a_value_the_document_does_not_have(tmp_path: Path) -> None:
    """Absence is labeled, not missing: a reading of one is a false positive."""
    labels = read_labels(a_set(tmp_path), tmp_path)

    assert labels.header("do0002.png")["document_id"] == ()


def test_a_cell_of_spaces_is_an_empty_cell(tmp_path: Path) -> None:
    text = LABELS.replace(",,590.00", ",  ,590.00")
    labels = read_labels(a_set(tmp_path, text), tmp_path)

    assert labels.header("do0002.png")["document_id"] == ()


def test_the_paper_column_is_kept_beside_the_labels_and_never_a_value(
    tmp_path: Path,
) -> None:
    labels = read_labels(a_set(tmp_path), tmp_path)

    assert labels.paper("do0001.png") == "thermal"
    assert "paper" not in labels.header("do0001.png")


def test_a_set_with_no_paper_column_has_no_paper(tmp_path: Path) -> None:
    text = "".join(
        line.rsplit(",", 1)[0] + "\n" for line in LABELS.splitlines(keepends=False)
    )
    labels = read_labels(a_set(tmp_path, text), tmp_path)

    assert labels.paper("do0001.png") is None


def test_a_regional_set_has_no_line_items_to_score(tmp_path: Path) -> None:
    labels = read_labels(a_set(tmp_path), tmp_path)

    assert labels.rows("do0001.png") == ()


def test_reads_a_file_a_spreadsheet_saved_with_a_byte_order_mark(
    tmp_path: Path,
) -> None:
    text = "﻿" + LABELS
    labels = read_labels(a_set(tmp_path, text), tmp_path)

    assert labels.header("do0001.png")["vendor_tax_id"] == ("130000001",)


def test_refuses_a_column_that_is_not_a_covered_fieldtype(tmp_path: Path) -> None:
    text = LABELS.replace("paper\n", "paper,vendor_email\n").replace(
        "thermal\n", "thermal,a@b.do\n"
    )
    text = text.replace("letter\n", "letter,\n")

    with pytest.raises(LabelsError, match="vendor_email"):
        read_labels(a_set(tmp_path, text), tmp_path)


def test_refuses_a_covered_fieldtype_with_no_column(tmp_path: Path) -> None:
    manifest = a_set(tmp_path, coverage=(*COVERAGE, "customer_tax_id"))

    with pytest.raises(LabelsError, match="customer_tax_id"):
        read_labels(manifest, tmp_path)


def test_refuses_a_file_naming_a_document_twice(tmp_path: Path) -> None:
    text = LABELS + "do0001.png,130000001,B0100000001,1180.00,thermal\n"

    with pytest.raises(LabelsError, match="do0001.png"):
        read_labels(a_set(tmp_path, text), tmp_path)


def test_refuses_labels_whose_digest_is_not_the_pinned_one(tmp_path: Path) -> None:
    manifest = a_set(tmp_path)
    (tmp_path / "labels.csv").write_text(
        LABELS.replace("1180.00", "1181.00"), encoding="utf-8"
    )

    with pytest.raises(LabelsError, match="digest"):
        read_labels(manifest, tmp_path)


def test_refuses_a_set_that_pins_no_digest_for_its_labels(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="labels.csv"):
        read_labels(a_set(tmp_path, files={}), tmp_path)


def test_refuses_a_set_that_declares_no_coverage(tmp_path: Path) -> None:
    with pytest.raises(LabelsError, match="coverage"):
        read_labels(a_set(tmp_path, coverage=None), tmp_path)


def test_refuses_labels_missing_a_pinned_document(tmp_path: Path) -> None:
    text = LABELS.rsplit("do0002.png", 1)[0]

    with pytest.raises(LabelsError, match="do0002.png"):
        read_labels(a_set(tmp_path, text), tmp_path)


def test_refuses_labels_for_a_document_the_manifest_does_not_pin(
    tmp_path: Path,
) -> None:
    text = LABELS + "do0003.png,130000003,B0100000003,10.00,letter\n"

    with pytest.raises(LabelsError, match="do0003.png"):
        read_labels(a_set(tmp_path, text), tmp_path)


def test_refuses_a_set_with_no_labels_file(tmp_path: Path) -> None:
    manifest = a_set(tmp_path)
    (tmp_path / "labels.csv").unlink()

    with pytest.raises(LabelsError, match="labels.csv"):
        read_labels(manifest, tmp_path)


def test_refuses_a_row_with_more_cells_than_columns(tmp_path: Path) -> None:
    text = LABELS.replace("thermal\n", "thermal,extra\n")

    with pytest.raises(LabelsError, match="do0001.png"):
        read_labels(a_set(tmp_path, text), tmp_path)


READING = Prediction(
    fields={
        "vendor_tax_id": "130000001",
        "document_id": "B0100000001",
        "amount_total_gross": "1180.00",
    }
)
"""do0001.png read right on every covered fieldtype."""


def scored(
    directory: Path,
    first: Prediction = READING,
    *,
    confidence: dict[str, Confidence] | None = None,
) -> SubsetScore:
    manifest = a_set(directory)
    return score_subset(
        read_labels(manifest, directory),
        manifest,
        {"do0001.png": first, "do0002.png": Prediction()},
        confidence=confidence,
    )


def test_a_reading_of_an_uncovered_fieldtype_is_neither_right_nor_wrong(
    tmp_path: Path,
) -> None:
    """Nobody labeled it, so nobody can say: the score is the covered one's."""
    uncovered = READING.model_copy(
        update={"fields": {**READING.fields, "vendor_email": "ventas@example.do"}}
    )

    with_it = scored(tmp_path, uncovered).fields
    without = scored(tmp_path).fields

    assert (with_it.true_positives, with_it.false_positives) == (3, 0)
    assert (without.true_positives, without.false_positives) == (3, 0)


def test_a_reading_of_a_value_labeled_absent_is_a_false_positive(
    tmp_path: Path,
) -> None:
    manifest = a_set(tmp_path)
    run = score_subset(
        read_labels(manifest, tmp_path),
        manifest,
        {"do0002.png": Prediction(fields={"document_id": "B0100000002"})},
    )

    assert run.fields.false_positives == 1


def test_a_covered_value_left_unread_is_a_false_negative(tmp_path: Path) -> None:
    run = scored(tmp_path)

    # do0002.png's two labeled values, read as nothing.
    assert run.fields.false_negatives == 2


def test_a_line_item_read_on_a_set_covering_no_cell_is_not_a_false_positive(
    tmp_path: Path,
) -> None:
    rows = READING.model_copy(
        update={
            "line_items": (
                {"line_item_description": "Arroz", "line_item_quantity": "1"},
            )
        }
    )

    run = scored(tmp_path, rows)

    assert run.line_items.false_positives == 0
    assert run.line_items.true_positives == 0


def test_the_breakdown_by_fieldtype_lists_only_covered_fieldtypes(
    tmp_path: Path,
) -> None:
    uncovered = READING.model_copy(
        update={"fields": {**READING.fields, "vendor_email": "ventas@example.do"}}
    )

    run = scored(tmp_path, uncovered)

    assert {each.fieldtype for each in run.per_fieldtype} == set(COVERAGE)


def test_calibration_judges_only_covered_values(tmp_path: Path) -> None:
    uncovered = READING.model_copy(
        update={"fields": {**READING.fields, "vendor_email": "ventas@example.do"}}
    )
    confidence = Confidence(
        fields={
            "vendor_tax_id": (0.95,),
            "document_id": (0.95,),
            "amount_total_gross": (0.95,),
            "vendor_email": (0.95,),
        }
    )

    run = scored(tmp_path, uncovered, confidence={"do0001.png": confidence})

    assert run.calibration is not None
    assert sum(bucket.n for bucket in run.calibration.header.buckets) == 3
