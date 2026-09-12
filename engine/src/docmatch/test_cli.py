"""Tests for the docmatch command line.

Every case builds a dataset from the synthetic fixture in a temporary
directory, so the suite runs in CI with no dataset present.
"""

import shutil
from pathlib import Path

import pytest

from docmatch.cli import main

FIXTURE = Path(__file__).parent / "docile" / "fixtures" / "synthetic_annotation.json"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    annotations = tmp_path / "annotations"
    annotations.mkdir()
    shutil.copy(FIXTURE, annotations / "syn0001.json")
    return tmp_path


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
