"""Tests for the docmatch command line.

Every case builds a dataset from the synthetic fixture in a temporary
directory, so the suite runs in CI with no dataset present.
"""

import json
from pathlib import Path

import pytest

from docmatch.cli import main
from docmatch.evals.manifest import load, select, write

EXPECTED_SHOW_OUTPUT = "\n".join(
    [
        "Document syn0001",
        "",
        "KILE fields (5)",
        "  vendor_name" + " " * 9 + "Synthetic Supplies Ltd",
        "  vendor_address" + " " * 6 + "12 Example Way",
        " " * 22 + "Testville, EX 00000",
        "  document_id" + " " * 9 + "SYN-0001",
        "  date_issue" + " " * 10 + "02/01/26",
        "  amount_total_gross" + " " * 2 + "236.00",
        "",
        # One column for the whole table, not one per line item: only item 2
        # carries a unit of measure, and it sets the width for both.
        "LIR line items (2)",
        "  line item 1",
        "    line_item_quantity" + " " * 10 + "2",
        "    line_item_description" + " " * 7 + "Blue widget",
        "    line_item_amount_gross" + " " * 6 + "100.00",
        "  line item 2",
        "    line_item_quantity" + " " * 10 + "1",
        "    line_item_units_of_measure" + " " * 2 + "EA",
        "    line_item_description" + " " * 7 + "Red widget",
        "    line_item_amount_gross" + " " * 6 + "136.00",
        "",
    ]
)


def test_show_prints_kile_fields_and_lir_line_items(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "syn0001", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


def test_show_reports_an_unknown_document_without_a_traceback(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "absent0001", "--data-dir", str(data_dir)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "absent0001" in captured.err


def test_show_points_at_the_readme_when_the_dataset_is_not_downloaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["show", "syn0001", "--data-dir", str(tmp_path / "absent")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "README.md" in captured.err


def test_data_dir_falls_back_to_the_environment(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The dataset lives outside the repo on some machines."""
    monkeypatch.setenv("DOCMATCH_DATA_DIR", str(data_dir))

    exit_code = main(["show", "syn0001"])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


def test_an_explicit_data_dir_beats_the_environment(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DOCMATCH_DATA_DIR", str(tmp_path / "absent"))

    exit_code = main(["show", "syn0001", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_SHOW_OUTPUT
    assert exit_code == 0


EXPECTED_HEADER_ONLY_OUTPUT = "\n".join(
    [
        "Document syn0002",
        "",
        "KILE fields (3)",
        "  vendor_name" + " " * 9 + "Synthetic Supplies Ltd",
        "  document_id" + " " * 9 + "SYN-0002",
        "  amount_total_gross" + " " * 2 + "40.00",
        "",
        "LIR line items (0)",
        "",
    ]
)


def test_show_handles_a_document_with_no_line_items(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plenty of real invoices carry no line-item table at all."""
    exit_code = main(["show", "syn0002", "--data-dir", str(data_dir)])

    assert capsys.readouterr().out == EXPECTED_HEADER_ONLY_OUTPUT
    assert exit_code == 0


def prediction_file(directory: Path, body: str) -> Path:
    path = directory / "prediction.json"
    path.write_text(body, encoding="utf-8")
    return path


PREDICTION = """
{
  "fields": {
    "vendor_name": "Synthetic  Supplies Ltd",
    "document_id": "SYN-0002",
    "date_issue": "February 1, 2026",
    "amount_total_gross": "$236.00",
    "date_due": null
  },
  "line_items": [
    {
      "line_item_quantity": "1",
      "line_item_description": "Red widget",
      "line_item_amount_gross": "$136.00",
      "line_item_tax": "0.00"
    },
    {
      "line_item_quantity": "2",
      "line_item_description": "Blue widget",
      "line_item_amount_gross": "100"
    }
  ]
}
"""
"""One reformatted header hit, one wrong, one missing, and the two rows of the
table in the other order: the first exact, the second short a unit of measure
and carrying a tax cell the label does not."""

EXPECTED_SCORE_OUTPUT = "\n".join(
    [
        "Document syn0001",
        "",
        "Field score",
        "  precision  0.750",
        "  recall     0.600",
        "  F1         0.667",
        "  3 matched, 2 missing, 1 spurious",
        "",
        "Fields (5)",
        "  amount_total_gross  matched   236",
        "  date_issue          matched   2026-02-01",
        "  document_id         missing   syn-0001",
        "                      spurious  syn-0002",
        "  vendor_address      missing   12 example way testville, ex 00000",
        "  vendor_name         matched   synthetic supplies ltd",
        "",
        # Every cell of the reordered rows lines up, so only the row short a
        # unit of measure is wrong, and only that fieldtype is inaccurate.
        "Line-item score",
        "  precision  0.500",
        "  recall     0.500",
        "  F1         0.500",
        "  1 matched, 1 missing, 1 spurious",
        "",
        "Cell accuracy (5)",
        "  line_item_amount_gross" + " " * 6 + "1.000  2 of 2",
        "  line_item_description" + " " * 7 + "1.000  2 of 2",
        "  line_item_quantity" + " " * 10 + "1.000  2 of 2",
        "  line_item_tax" + " " * 15 + "       0 of 0, 1 spurious",
        "  line_item_units_of_measure  0.000  0 of 1",
        "",
    ]
)


def test_score_reports_both_numbers_and_what_went_into_them(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, PREDICTION)

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    assert capsys.readouterr().out == EXPECTED_SCORE_OUTPUT
    assert exit_code == 0


def test_score_reports_an_unreadable_prediction_without_a_traceback(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, '{"fields": {"vendor_name": {"text": "Acme"}}}')

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert str(path) in captured.err


def test_score_reports_a_prediction_file_that_is_not_there(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        ["score", "syn0001", "--prediction", str(absent), "--data-dir", str(data_dir)]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


EXPECTED_HEADER_ONLY_SCORE = "\n".join(
    [
        "Document syn0002",
        "",
        "Field score",
        "  precision  1.000",
        "  recall     0.333",
        "  F1         0.500",
        "  1 matched, 2 missing, 0 spurious",
        "",
        "Fields (3)",
        "  amount_total_gross  missing   40",
        "  document_id         missing   syn-0002",
        "  vendor_name         matched   synthetic supplies ltd",
        "",
        # No table on either side is an agreement about nothing, not a zero:
        # 355 of the 5,680 annotated documents carry no line items at all.
        "Line-item score",
        "  precision  1.000",
        "  recall     1.000",
        "  F1         1.000",
        "  0 matched, 0 missing, 0 spurious",
        "",
        "Cell accuracy (0)",
        "",
    ]
)


def test_score_handles_a_document_with_no_line_items(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(
        tmp_path, '{"fields": {"vendor_name": "Synthetic Supplies Ltd"}}'
    )

    exit_code = main(
        ["score", "syn0002", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    assert capsys.readouterr().out == EXPECTED_HEADER_ONLY_SCORE
    assert exit_code == 0


def test_score_reports_rows_predicted_for_a_document_that_has_none(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recall is vacuously whole; precision is what an invented table costs."""
    path = prediction_file(
        tmp_path, '{"line_items": [{"line_item_description": "Invented"}]}'
    )

    exit_code = main(
        ["score", "syn0002", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  0 matched, 0 missing, 1 spurious" in out
    assert "  line_item_description         0 of 0, 1 spurious" in out


def test_score_names_the_row_and_the_fieldtype_of_a_bad_cell(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Which half of a prediction is wrong is not enough to find the cell by."""
    path = prediction_file(
        tmp_path, '{"line_items": [{}, {"line_item_quantity": {"n": 2}}]}'
    )

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "line_items.1.line_item_quantity is the first problem" in captured.err


def test_score_reports_a_prediction_written_at_the_top_level(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fieldtypes outside "fields" would otherwise score as an empty prediction."""
    path = prediction_file(tmp_path, '{"vendor_name": "Acme"}')

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "vendor_name" in captured.err


def test_score_still_reports_the_dataset_errors_show_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, "{}")

    exit_code = main(
        [
            "score",
            "syn0001",
            "--prediction",
            str(path),
            "--data-dir",
            str(tmp_path / "absent"),
        ]
    )

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


@pytest.fixture
def split_dir(tmp_path: Path) -> Path:
    """A dataset whose val split is large enough to draw a subset from.

    `subset` reads the split file and no annotation, so the documents it names
    need not exist; the annotations directory is there because that is how a
    downloaded dataset is told apart from a path that was never downloaded.
    """
    root = tmp_path / "dataset"
    (root / "annotations").mkdir(parents=True)
    ids = [f"doc{number:04d}" for number in range(500)]
    (root / "val.json").write_text(json.dumps(ids), encoding="utf-8")
    return root


def test_subset_writes_the_manifest_it_draws(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "subset.json"

    exit_code = main(
        ["subset", "--write", "--manifest", str(path), "--data-dir", str(split_dir)]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "Fixed subset",
            f"  manifest  {path}",
            "  split     val",
            "  seed      20260912",
            "  size      100",
            "  written   yes",
            "",
        ]
    )
    assert len(load(path).document_ids) == 100


def test_subset_reports_that_the_seed_still_draws_the_pinned_documents(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "subset.json"
    main(["subset", "--write", "--manifest", str(path), "--data-dir", str(split_dir)])
    capsys.readouterr()

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 0
    assert "  reproduced  yes" in capsys.readouterr().out


def test_subset_fails_when_the_seed_no_longer_draws_the_pinned_documents(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A subset that drifted is not a benchmark, so this is not a warning."""
    path = tmp_path / "subset.json"
    main(["subset", "--write", "--manifest", str(path), "--data-dir", str(split_dir)])
    capsys.readouterr()
    pinned = load(path)
    undrawn = next(
        f"doc{number:04d}"
        for number in range(500)
        if f"doc{number:04d}" not in pinned.document_ids
    )
    write(
        pinned.model_copy(update={"document_ids": (undrawn, *pinned.document_ids[1:])}),
        path,
    )

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "  reproduced  no, 1 of the 100 documents are not pinned" in (
        capsys.readouterr().out
    )


def test_subset_reports_the_pinned_documents_in_the_wrong_order(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A prefix of the subset is a sample, so the order is pinned too."""
    path = tmp_path / "subset.json"
    main(["subset", "--write", "--manifest", str(path), "--data-dir", str(split_dir)])
    capsys.readouterr()
    pinned = load(path)
    write(
        pinned.model_copy(
            update={"document_ids": tuple(reversed(pinned.document_ids))}
        ),
        path,
    )

    exit_code = main(["subset", "--manifest", str(path), "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "  reproduced  no, the same documents in a different order" in (
        capsys.readouterr().out
    )


def test_subset_draws_with_the_seed_it_is_given(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "subset.json"

    main(
        [
            "subset",
            "--write",
            "--seed",
            "1",
            "--manifest",
            str(path),
            "--data-dir",
            str(split_dir),
        ]
    )

    assert "  seed      1\n" in capsys.readouterr().out
    assert load(path).document_ids == select(
        [f"doc{number:04d}" for number in range(500)], seed=1, size=100
    )


def test_subset_reports_a_manifest_that_is_not_there(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        ["subset", "--manifest", str(absent), "--data-dir", str(split_dir)]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


def test_subset_points_at_the_readme_when_the_dataset_is_not_downloaded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["subset", "--data-dir", str(tmp_path / "absent")])

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


def test_subset_reports_a_split_the_dataset_does_not_have(
    split_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An incomplete download is a different problem to a drifted subset."""
    (split_dir / "val.json").unlink()

    exit_code = main(["subset", "--data-dir", str(split_dir)])

    assert exit_code == 1
    assert "val.json" in capsys.readouterr().err
