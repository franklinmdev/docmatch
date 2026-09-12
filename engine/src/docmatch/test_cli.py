"""Tests for the docmatch command line.

Every case builds a dataset from the synthetic fixture in a temporary
directory, so the suite runs in CI with no dataset present.
"""

import json
from pathlib import Path

import pytest

from docmatch.cli import main
from docmatch.evals.manifest import Manifest, load, select, write

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


def corpus_report(data_dir: Path) -> str:
    """The whole report for the two synthetic documents, as the terminal prints it."""
    return "\n".join(
        [
            f"Corpus  {data_dir}, val",
            "  documents                2",
            "  KILE labels              8",
            "  LIR cells                7",
            "  line items               2",
            "  documents with no table  1",
            "  largest table            2",
            "",
            "Rules read, of the header labels",
            "  date    100.0%  1 of 1",
            "  number  100.0%  2 of 2",
            "  text    100.0%  5 of 5",
            "",
            "Rules read, of the line-item cells",
            "  number  100.0%  4 of 4",
            "  text    100.0%  3 of 3",
            "",
            "Fieldtypes (9)",
            "  amount_total_gross          number  2  100.0%",
            "  date_issue                  date    1  100.0%",
            # The number rule reads `SYN-0001` as -1, which is the whole reason
            # an identifier is text.
            "  document_id                 text    2          100.0% as a number",
            "  line_item_amount_gross      number  2  100.0%",
            "  line_item_description       text    2            0.0% as a number",
            "  line_item_quantity          number  2  100.0%",
            "  line_item_units_of_measure  text    1            0.0% as a number",
            "  vendor_address              text    1            0.0% as a number",
            "  vendor_name                 text    2            0.0% as a number",
            "",
            "metrics/normalization.py",
            "  numeric dates proving month first           0",
            "  numeric dates proving day first             0",
            "  dates the month rule's strictness turns on  0",
            "  numbers written with a lone dot and three digits (0)",
            "",
            "metrics/fields.py",
            "  header fieldtypes labeled more than once  0",
            "    of those, one value written twice       0",
            "  labels repeating a line inside one box    0",
            "",
            "metrics/line_items.py",
            "  rows repeating a fieldtype           0",
            "    of those, still after normalizing  0",
            "",
        ]
    )


def test_corpus_counts_the_dataset_under_the_module_that_asserts_each_count(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["corpus", "--data-dir", str(data_dir), "--split", "val"])

    assert capsys.readouterr().out == corpus_report(data_dir)
    assert exit_code == 0


def test_corpus_is_skipped_rather_than_failed_without_a_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI has no dataset, and a survey it cannot take is not a broken engine."""
    absent = tmp_path / "absent"

    exit_code = main(["corpus", "--data-dir", str(absent)])

    captured = capsys.readouterr()
    assert exit_code == 0, "a missing dataset must not fail the command"
    assert captured.out == f"Corpus\n  skipped, no dataset at {absent}\n"
    assert captured.err == ""


def test_corpus_still_reports_a_dataset_that_lacks_the_split(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A downloaded dataset missing `trainval` is wrong, not absent."""
    exit_code = main(["corpus", "--data-dir", str(data_dir)])

    assert exit_code == 1
    assert "trainval.json" in capsys.readouterr().err


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


EXPECTED_EVAL_OUTPUT = "\n".join(
    [
        "Fixed subset",
        "  manifest       {manifest}",
        "  split          val",
        "  size           5",
        "  predicted      4",
        "  not predicted  1",
        "  not pinned     1",
        "",
        # A pinned document nobody predicted and a predicted document nobody
        # pinned are both named, because a count cannot be acted on.
        "Not predicted (1)",
        "  eval0006",
        "",
        "Not pinned (1)",
        "  eval0001",
        "",
        "Field score",
        "  precision  0.875",
        "  recall     0.700",
        "  F1         0.778",
        "  14 matched, 6 missing, 2 spurious",
        "",
        "Fields (6)",
        "  amount_total_gross  0.750  3 matched, 2 missing",
        "  date_issue          0.800  2 matched, 1 missing",
        "  document_id         0.667  3 matched, 2 missing, 1 spurious",
        "  tax_detail_rate     1.000  2 matched, 0 missing",
        "  vendor_email        0.000  0 matched, 0 missing, 1 spurious",
        "  vendor_name         0.889  4 matched, 1 missing",
        "",
        "Line-item score",
        "  precision  0.571",
        "  recall     0.571",
        "  F1         0.571",
        "  4 matched, 3 missing, 3 spurious",
        "",
        "Cell accuracy (3)",
        "  line_item_amount_gross  0.714  5 of 7, 2 spurious",
        "  line_item_description   0.857  6 of 7, 1 spurious",
        "  line_item_quantity      0.714  5 of 7, 1 spurious",
        "",
    ]
)
"""What CI sees. The counts behind it are checked in `evals/test_run.py`; this
pins the report that carries them."""


def evaluate(synthetic_subset: Path, *arguments: str) -> list[str]:
    return [
        "eval",
        "--data-dir",
        str(synthetic_subset),
        "--manifest",
        str(synthetic_subset / "subset.json"),
        "--predictions",
        str(synthetic_subset / "predictions.json"),
        *arguments,
    ]


def test_eval_reports_both_numbers_over_the_subset(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(evaluate(synthetic_subset))

    assert capsys.readouterr().out == EXPECTED_EVAL_OUTPUT.format(
        manifest=synthetic_subset / "subset.json"
    )
    assert exit_code == 0


def test_eval_prints_no_label_text(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 6: the report is pasted into a commit message, so it carries counts."""
    main(evaluate(synthetic_subset))

    out = capsys.readouterr().out
    assert "Junction box" not in out
    assert "Beacon" not in out


def test_eval_scores_the_committed_subset_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command in the README names a predictions file and nothing else."""
    predictions = tmp_path / "predictions.json"
    predictions.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--predictions",
            str(predictions),
            "--data-dir",
            str(tmp_path / "absent"),
        ]
    )

    assert exit_code == 1
    assert "README.md" in capsys.readouterr().err


def test_eval_reports_a_predictions_file_that_is_not_there(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.json"

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(synthetic_subset / "subset.json"),
            "--predictions",
            str(absent),
        ]
    )

    assert exit_code == 1
    assert str(absent) in capsys.readouterr().err


def test_eval_names_the_document_of_a_bad_prediction(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "predictions.json"
    path.write_text('{"eval0002": {"fields": {"vendor_name": 7}}}', encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(synthetic_subset / "subset.json"),
            "--predictions",
            str(path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "eval0002.fields.vendor_name is the first problem" in captured.err


def test_eval_reports_a_pinned_document_the_dataset_does_not_hold(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dataset that cannot satisfy the manifest produces no number at all."""
    manifest = tmp_path / "subset.json"
    write(Manifest(split="val", seed=1, size=1, document_ids=("eval9999",)), manifest)
    predictions = tmp_path / "predictions.json"
    predictions.write_text("{}", encoding="utf-8")

    exit_code = main(
        [
            "eval",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(manifest),
            "--predictions",
            str(predictions),
        ]
    )

    assert exit_code == 1
    assert "eval9999" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--seed", "--size"])
def test_subset_refuses_to_draw_without_writing(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Checking the pinned seed while asked about another one is a wrong answer."""
    with pytest.raises(SystemExit):
        main(["subset", flag, "5"])

    assert "--write" in capsys.readouterr().err
