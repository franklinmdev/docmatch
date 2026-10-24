"""Tests for `docmatch export 606` on the committed regional fixture.

The fixture's comprobantes, numbers and amounts are made up
(`evals.regional_fixture`); the expected lines are written out by hand from
its labels, its readings and its `filing.csv`.
"""

import json
import shutil
from pathlib import Path

import pytest

from docmatch.cli import main

REGIONAL = Path(__file__).parents[2] / "tests" / "evals" / "regional"
SET = REGIONAL / "do" / "manifest.json"
RUN = REGIONAL / "run"

MARCH = "DGII_F_606_130990026_202603.TXT"
APRIL = "DGII_F_606_130990026_202604.TXT"
MAY = "DGII_F_606_130990026_202605.TXT"
JUNE = "DGII_F_606_130990026_202606.TXT"

MARCH_CONTENT = (
    "606|130990026|202603|2\r\n"
    "130990018|1|09|B0100000417||20260314||0.00|2500.00|2500.00|375.00|0.00|"
    "0.00|0.00|375.00|0.00||0.00|0.00|50.00|25.00|0.00|4\r\n"
    "130990018|1|09|B0400000009|B0100000417|20260320||0.00|500.00|500.00|75.00|"
    "0.00|0.00|75.00|0.00|0.00||0.00|0.00|0.00|0.00|0.00|6"
)
"""The B01 and the nota de credito modifying it, read right on both sides."""

MAY_LABELS = (
    "606|130990026|202605|1\r\n"
    "130990042|1|02|E310000000052||20260521|20260530|8000.00|0.00|8000.00|"
    "1000.00|750.00|1000.00|0.00|1000.00|0.00|02|800.00|0.00|0.00|0.00|0.00|2"
)
APRIL_CONTENT = (
    "606|130990026|202604|1\r\n"
    "00099000028|2|02|B0100000500||20260412|20260620|5000.00|0.00|5000.00|0.00|"
    "0.00|0.00|0.00|0.00|0.00|02|500.00|0.00|0.00|0.00|0.00|2"
)
"""Fees paid 2026-06-20, retention not printed: 10 percent of the services."""

RENT_LINE = (
    "00099000036|2|03|B0100000501||20260625|20260715|6000.00|0.00|6000.00|0.00|"
    "0.00|0.00|0.00|0.00|0.00|01|900.00|0.00|0.00|0.00|0.00|2"
)
"""Rent dated in June and paid 2026-07-15: 15 percent, the payment date's."""

JUNE_LABELS = f"606|130990026|202606|1\r\n{RENT_LINE}"
JUNE_READING = (
    "606|130990026|202606|2\r\n"
    "130990069|1|09|E310000000019||20260610||0.00|400.00|400.00|72.00|0.00|0.00|"
    "0.00|72.00|0.00||0.00|0.00|0.00|0.00|0.00|1\r\n" + RENT_LINE
)
"""The reading misread the E32 as an E31, which the control leaves out."""

EXPECTED_OUTPUT = "\n".join(
    [
        "606 export",
        "  manifest      {manifest}",
        "  run           {run}",
        "  out           {out}",
        "  documents     8",
        "  not reported  1",
        "",
        # The reading left the E31's ITBIS out, which leaves its printed
        # ITBIS retention above an ITBIS of zero: its month is not filed.
        "Reading, 3 of 4 monthly files written",
        "  202603  2 rows",
        "  202604  1 row",
        "  202605  failed, 1 document: ITBIS retention above the ITBIS",
        "  202606  2 rows",
        "  left out  202603  1 document, consumo of RD$250,000.00 or less",
        "",
        "Labels control, 4 of 4 monthly files written",
        "  202603  2 rows",
        "  202604  1 row",
        "  202605  1 row",
        "  202606  1 row",
        "  left out  202603  1 document, consumo of RD$250,000.00 or less",
        "  left out  202606  1 document, consumo of RD$250,000.00 or less",
        "",
    ]
)


LEFT_OUT_B02 = {
    "filename": "do0005.png",
    "period": "202603",
    "reason": "consumo of RD$250,000.00 or less",
}
LEFT_OUT_E32 = {
    "filename": "do0006.png",
    "period": "202606",
    "reason": "consumo of RD$250,000.00 or less",
}


def exporting(out: Path, manifest: Path = SET, run: Path = RUN) -> list[str]:
    return [
        "export",
        "606",
        "--manifest",
        str(manifest),
        "--run",
        str(run),
        "--out",
        str(out),
    ]


def test_export_writes_one_606_per_month_for_the_reading_and_the_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "606"

    exit_code = main(exporting(out))

    assert exit_code == 0
    assert capsys.readouterr().out == EXPECTED_OUTPUT.format(
        manifest=SET, run=RUN, out=out
    )
    assert {
        path.relative_to(out).as_posix(): path.read_bytes().decode()
        for path in out.rglob("*.TXT")
    } == {
        f"reading/{MARCH}": MARCH_CONTENT,
        f"reading/{APRIL}": APRIL_CONTENT,
        f"reading/{JUNE}": JUNE_READING,
        f"control/{MARCH}": MARCH_CONTENT,
        f"control/{APRIL}": APRIL_CONTENT,
        f"control/{MAY}": MAY_LABELS,
        f"control/{JUNE}": JUNE_LABELS,
    }


def test_export_lists_every_month_and_document_in_export_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What `prevalidate 606` reads (#229): every month's file, or why none."""
    out = tmp_path / "606"
    main(exporting(out))

    listed = json.loads((out / "export.json").read_text())

    assert listed == {
        "manifest": str(SET),
        "run": str(RUN),
        "not_reported": [
            {"filename": "do0002.png", "reason": "annotated not reported"}
        ],
        "reading": [
            {"period": "202603", "file": f"reading/{MARCH}", "rows": 2, "failures": []},
            {"period": "202604", "file": f"reading/{APRIL}", "rows": 1, "failures": []},
            {
                "period": "202605",
                "file": None,
                "rows": 0,
                "failures": [
                    {
                        "filename": "do0003.png",
                        "reasons": ["ITBIS retention above the ITBIS"],
                    }
                ],
            },
            {"period": "202606", "file": f"reading/{JUNE}", "rows": 2, "failures": []},
        ],
        "control": [
            {"period": "202603", "file": f"control/{MARCH}", "rows": 2, "failures": []},
            {"period": "202604", "file": f"control/{APRIL}", "rows": 1, "failures": []},
            {"period": "202605", "file": f"control/{MAY}", "rows": 1, "failures": []},
            {"period": "202606", "file": f"control/{JUNE}", "rows": 1, "failures": []},
        ],
        "reading_left_out": [LEFT_OUT_B02],
        "control_left_out": [LEFT_OUT_B02, LEFT_OUT_E32],
    }


def test_the_printed_report_shows_no_number_or_name_from_a_comprobante(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(exporting(tmp_path / "606"))

    output = capsys.readouterr().out
    for value in ("130990026", "130990018", "B0100000417", "Ferreteria", "do0002"):
        assert value not in output


def a_run_without(tmp_path: Path, filename: str) -> Path:
    """The fixture's run with one document's reading taken out, as a failed
    extraction leaves it."""
    run = tmp_path / "run"
    shutil.copytree(RUN, run)
    predictions = json.loads((run / "predictions.json").read_text())
    del predictions[filename]
    (run / "predictions.json").write_text(json.dumps(predictions))
    return run


def test_a_document_never_read_fails_the_no_month_row_of_the_reading_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = a_run_without(tmp_path, "do0003.png")
    out = tmp_path / "606"

    assert main(exporting(out, run=run)) == 0

    output = capsys.readouterr().out
    assert "Reading, 3 of 4 monthly files written" in output
    assert "  no month  failed, 1 document: no reading" in output
    assert "Labels control, 4 of 4 monthly files written" in output
    assert sorted(path.name for path in (out / "reading").iterdir()) == [
        MARCH,
        APRIL,
        JUNE,
    ]


def test_a_month_with_a_document_missing_its_ncf_writes_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    shutil.copytree(RUN, run)
    predictions = json.loads((run / "predictions.json").read_text())
    predictions["do0004.png"]["fields"]["document_id"] = None
    (run / "predictions.json").write_text(json.dumps(predictions))
    out = tmp_path / "606"

    main(exporting(out, run=run))

    output = capsys.readouterr().out
    assert "  202603  failed, 1 of 2 documents: no NCF" in output
    listed = json.loads((out / "export.json").read_text())
    assert listed["reading"][0] == {
        "period": "202603",
        "file": None,
        "rows": 1,
        "failures": [{"filename": "do0004.png", "reasons": ["no NCF"]}],
    }


def test_export_refuses_a_directory_that_already_holds_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "606"
    out.mkdir()
    (out / "kept.txt").write_text("")

    assert main(exporting(out)) == 1

    assert "not empty" in capsys.readouterr().err
    assert sorted(path.name for path in out.iterdir()) == ["kept.txt"]


def a_set_with(tmp_path: Path, **changes: object) -> Path:
    """The fixture's set copied, its manifest changed, unless the value is
    None, which removes the key."""
    directory = tmp_path / "do"
    shutil.copytree(REGIONAL / "do", directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    for key, value in changes.items():
        if value is None:
            del manifest[key]
        else:
            manifest[key] = value
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory / "manifest.json"


def test_export_refuses_a_set_of_another_region(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 606 is DGII's; a set of another region has no such report."""
    manifest = a_set_with(tmp_path, region="pr")

    assert main(exporting(tmp_path / "606", manifest=manifest)) == 1

    assert "region pr" in capsys.readouterr().err


def test_export_refuses_a_run_over_another_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(exporting(tmp_path / "606", run=REGIONAL / "ecf-run")) == 1

    assert "is not the pinned subset" in capsys.readouterr().err


def test_export_refuses_a_set_with_no_reporter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = a_set_with(tmp_path, reporter=None)

    assert main(exporting(tmp_path / "606", manifest=manifest)) == 1

    assert "reporter" in capsys.readouterr().err
