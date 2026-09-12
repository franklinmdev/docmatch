"""Tests for the docmatch command line.

Every case builds a dataset from the synthetic fixture in a temporary
directory, so the suite runs in CI with no dataset present.
"""

from pathlib import Path

import pytest

from docmatch.cli import main

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
    ]
)


def test_score_reports_the_number_and_every_fieldtype(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hand-written prediction: one reformatted hit, one wrong, one missing."""
    path = prediction_file(
        tmp_path,
        """
        {
          "vendor_name": "Synthetic  Supplies Ltd",
          "document_id": "SYN-0002",
          "date_issue": "February 1, 2026",
          "amount_total_gross": "$236.00",
          "date_due": null
        }
        """,
    )

    exit_code = main(
        ["score", "syn0001", "--prediction", str(path), "--data-dir", str(data_dir)]
    )

    assert capsys.readouterr().out == EXPECTED_SCORE_OUTPUT
    assert exit_code == 0


def test_score_reports_an_unreadable_prediction_without_a_traceback(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = prediction_file(tmp_path, '{"vendor_name": {"text": "Acme"}}')

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
