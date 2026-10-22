"""Tests for `docmatch failures` through the CLI: the committed synthetic
fixture's saved run and hand-built loop runs, no Postgres and no model.

Kept beside `test_cli.py` rather than inside it, so the command's tests read
in one place.
"""

import json
import re
from pathlib import Path

import pytest

from docmatch.cli import main
from docmatch.evals.failures import ROW_CLASSES, VALUE_CLASSES
from docmatch.gate import FIELDTYPES as GATE_FIELDTYPES
from docmatch.matching.matcher import TYPES
from docmatch.pipeline.saved import write_loop_run
from docmatch.pipeline.test_ladder import CLEAN, PRICE_HELD, case, run


def failures(synthetic_subset: Path, *arguments: str) -> list[str]:
    return [
        "failures",
        "--data-dir",
        str(synthetic_subset),
        "--manifest",
        str(synthetic_subset / "subset.json"),
        *arguments,
    ]


EXPECTED_FAILURES_OUTPUT = "\n".join(
    [
        "Error classes, gemini synthetic-001, over 5 pinned documents",
        "",
        "  header value   count  false negatives  false positives",
        "  net for gross      0                0                0",
        "  misread date       2                2                2",
        "  wrong              1                1                1",
        "  missing            5                5                0",
        "  spurious           2                0                2",
        "  total             10                8                5",
        "  field F1 counts 8 false negatives and 5 false positives",
        "",
        "  row           count  false negatives  false positives",
        "  wrong row         2                2                2",
        "  missing row       1                1                0",
        "  spurious row      1                0                1",
        "  total             4                3                3",
        "  line-item F1 counts 3 false negatives and 3 false positives",
        "",
        "  cell of a wrong row  count",
        "  net for gross            0",
        "  misread date             0",
        "  wrong                    1",
        "  missing                  1",
        "  spurious                 0",
        "  total                    2",
        "  2 of 2 wrong rows are off by one cell",
        "",
        "Header values by class and fieldtype, most first",
        "  class, fieldtype                    count",
        "  misread date, date_due                  2",
        "  missing, amount_total_gross             2",
        "  wrong, document_id                      1",
        "  missing, date_issue                     1",
        "  missing, document_id                    1",
        "  missing, vendor_name                    1",
        "  spurious, currency_code_amount_due      1",
        "  spurious, vendor_email                  1",
        "",
        "Cells of wrong rows by class and fieldtype, most first",
        "  class, fieldtype               count",
        "  wrong, line_item_amount_gross      1",
        "  missing, line_item_quantity        1",
        "",
    ]
)
"""What the command prints over the fixture's saved run. The counts behind
it are checked in `evals/test_failures.py`; this pins the report."""


def test_failures_reports_error_classes_for_a_saved_run(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(failures(synthetic_subset, "--run", str(synthetic_subset)))

    assert capsys.readouterr().out == EXPECTED_FAILURES_OUTPUT
    assert exit_code == 0


def fixture_texts(synthetic_subset: Path) -> tuple[set[str], set[str]]:
    """Every label and reading text in the fixture's pinned documents, and
    every fieldtype they are filed under."""
    pinned = json.loads((synthetic_subset / "subset.json").read_text("utf-8"))
    texts: set[str] = set()
    fieldtypes: set[str] = set()
    for document_id in pinned["document_ids"]:
        annotation = json.loads(
            (synthetic_subset / "annotations" / f"{document_id}.json").read_text(
                "utf-8"
            )
        )
        for each in (
            annotation["field_extractions"] + annotation["line_item_extractions"]
        ):
            texts.add(each["text"])
            fieldtypes.add(each["fieldtype"])
    predictions = json.loads((synthetic_subset / "predictions.json").read_text("utf-8"))
    for reading in predictions.values():
        for fields in [reading.get("fields", {}), *reading.get("line_items", [])]:
            fieldtypes.update(fields)
            for value in fields.values():
                if isinstance(value, str):
                    texts.add(value)
                elif isinstance(value, list):
                    texts.update(value)
    return texts, fieldtypes


ALLOWED_WORDS = {
    *(word for kind in (*VALUE_CLASSES, *ROW_CLASSES) for word in kind.split()),
    *re.findall(
        r"[a-z]+",
        """error classes gemini synthetic over pinned documents header value
        count false negatives positives total field counts and row line item
        cell of a wrong rows are off by one class fieldtype values most first
        cells""",
    ),
}


def test_failures_prints_only_class_names_fieldtypes_and_numbers(
    synthetic_subset: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 6: the report goes in the README, so no value a document says
    reaches it. Every word is a class name, a fieldtype, a heading word or a
    number, and no label or reading text of the fixture appears in it."""
    main(failures(synthetic_subset, "--run", str(synthetic_subset)))
    out = capsys.readouterr().out

    texts, fieldtypes = fixture_texts(synthetic_subset)

    words = set(re.findall(r"[a-z][a-z_]*", out.lower()))
    assert words - fieldtypes - ALLOWED_WORDS - {"f"} == set()
    leaked = [
        text
        for text in texts
        if len(text) >= 3 and re.search(r"[A-Za-z]", text) and text in out
    ]
    assert leaked == []


def test_failures_breaks_a_loop_runs_escapes_down_by_cause(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loop runs alone need no labels, so no dataset is read."""
    saved = run(
        case("unit", truth=("unit variant",)),
        case("both", truth=("unit variant",), misread=("date_due",)),
        PRICE_HELD,
        CLEAN,
    )
    write_loop_run(saved, tmp_path / "loop")

    exit_code = main(
        [
            "failures",
            "--data-dir",
            str(tmp_path / "no-dataset"),
            "--loop",
            str(tmp_path / "loop"),
        ]
    )

    assert capsys.readouterr().out == "\n".join(
        [
            "Escaped documents at P4, gemini",
            "  documents           4 of 4",
            "  approved            3",
            "  escaped             2",
            "  injected hold       2",
            "  misread gate value  1",
            "",
            "  injected hold type  documents",
            "  unit variant                2",
            "",
            "  misread gate fieldtype  documents",
            "  date_due                        1",
            "  a document counts once under each cause, type and fieldtype it carries,",
            "  so the breakdowns can sum above the escapes",
            "",
        ]
    )
    assert exit_code == 0


def test_failures_escapes_print_only_types_fieldtypes_and_numbers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_loop_run(run(CLEAN), tmp_path / "loop")

    main(["failures", "--loop", str(tmp_path / "loop")])
    out = capsys.readouterr().out

    allowed = {
        *(word for kind in TYPES for word in re.findall(r"[a-z]+", kind)),
        *GATE_FIELDTYPES,
        *re.findall(
            r"[a-z]+",
            """escaped documents at gemini of approved injected hold misread
            gate value type fieldtype none a document counts once under each
            cause and it carries so the breakdowns can sum above escapes""",
        ),
    }
    assert set(re.findall(r"[a-z][a-z_]*", out.lower())) - allowed == {"p"}


def test_failures_needs_a_run_or_a_loop(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["failures"])

    assert raised.value.code == 2
    assert "give at least one --run or --loop" in capsys.readouterr().err


def test_failures_refuses_a_run_over_another_subset(
    synthetic_subset: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = json.loads((synthetic_subset / "subset.json").read_text("utf-8"))
    other["seed"] = 2
    (tmp_path / "subset.json").write_text(json.dumps(other), "utf-8")

    exit_code = main(
        [
            "failures",
            "--data-dir",
            str(synthetic_subset),
            "--manifest",
            str(tmp_path / "subset.json"),
            "--run",
            str(synthetic_subset),
        ]
    )

    assert exit_code == 1
    assert "is not the pinned subset" in capsys.readouterr().err
