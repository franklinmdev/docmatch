"""Tests for `docmatch prevalidate 606` over the committed regional fixture.

The fixture's export is written by `export 606` itself; the container never
runs here (#229). The runner is replaced by one answering with what DGII's
pre-validator printed on made-up files, captured in
`export/test_prevalidate.py`, and it records what it was asked to check.
"""

from pathlib import Path

import pytest

from docmatch import cli
from docmatch.cli import main
from docmatch.export.prevalidate import BINARY_SHA256, Runner
from docmatch.export.test_prevalidate import HEADER_ONLY, NCF_ON_LINE_2, PASSED

REGIONAL = Path(__file__).parents[2] / "tests" / "evals" / "regional"
SET = REGIONAL / "do" / "manifest.json"
RUN = REGIONAL / "run"

EXPECTED_OUTPUT = "\n".join(
    [
        "606 pre-validation",
        "  export        {export}",
        f"  binary        Pre_Validado.zip sha256 {BINARY_SHA256}",
        "  version       MSI 2.6, exe 4.0.0.9",
        "  runtime       Mono 6.12.0.182, a pass uncompared with one on Windows",
        "  not reported  1 (annotated not reported)",
        "",
        # May never reached the pre-validator: the export failed it, and its
        # one document is a row with an error.
        "Reading, 3 of 4 monthly files with zero errors, 1 of 6 rows with an error",
        "  202603  0 errors, 2 rows",
        "  202604  0 errors, 1 row",
        "  202605  failed by the export, 1 document: ITBIS retention above the ITBIS",
        "  202606  0 errors, 2 rows",
        "",
        "Labels control, 4 of 4 monthly files with zero errors, "
        "0 of 5 rows with an error",
        "  202603  0 errors, 2 rows",
        "  202604  0 errors, 1 row",
        "  202605  0 errors, 1 row",
        "  202606  0 errors, 1 row",
        "",
    ]
)


def exported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    out = tmp_path / "606"
    assert (
        main(
            [
                "export",
                "606",
                "--manifest",
                str(SET),
                "--run",
                str(RUN),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    capsys.readouterr()
    return out


def answering(
    monkeypatch: pytest.MonkeyPatch,
    asked: list[tuple[str, str, str]],
    answers: dict[str, str] | None = None,
) -> None:
    """Replace the container with a runner that answers a pass, or the
    answer given for a side and file, and records each file it is handed."""

    def runner(binary: Path) -> Runner:
        def run(path: Path, reporter: str, period: str) -> str:
            name = f"{path.parent.name}/{path.name}"
            asked.append((name, reporter, period))
            return (answers or {}).get(name, PASSED)

        return run

    monkeypatch.setattr(cli, "container_runner", runner)


def prevalidating(export: Path, binary: Path) -> list[str]:
    return ["prevalidate", "606", "--export", str(export), "--binary", str(binary)]


def test_prevalidate_prints_files_and_rows_for_the_reading_and_the_control(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = exported(tmp_path, capsys)
    asked: list[tuple[str, str, str]] = []
    answering(monkeypatch, asked)

    exit_code = main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    assert exit_code == 0
    assert capsys.readouterr().out == EXPECTED_OUTPUT.format(export=export)


def test_only_the_files_the_export_wrote_are_run_typed_with_their_header(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The month the export failed is not handed to the pre-validator."""
    export = exported(tmp_path, capsys)
    asked: list[tuple[str, str, str]] = []
    answering(monkeypatch, asked)

    main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    assert asked == [
        ("reading/DGII_F_606_130990026_202603.TXT", "130990026", "202603"),
        ("reading/DGII_F_606_130990026_202604.TXT", "130990026", "202604"),
        ("reading/DGII_F_606_130990026_202606.TXT", "130990026", "202606"),
        ("control/DGII_F_606_130990026_202603.TXT", "130990026", "202603"),
        ("control/DGII_F_606_130990026_202604.TXT", "130990026", "202604"),
        ("control/DGII_F_606_130990026_202605.TXT", "130990026", "202605"),
        ("control/DGII_F_606_130990026_202606.TXT", "130990026", "202606"),
    ]


def test_an_error_fails_its_file_and_its_row_with_no_value_printed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = exported(tmp_path, capsys)
    answering(
        monkeypatch,
        [],
        {"reading/DGII_F_606_130990026_202603.TXT": NCF_ON_LINE_2},
    )

    main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    output = capsys.readouterr().out
    assert (
        "Reading, 2 of 4 monthly files with zero errors, 2 of 6 rows with an error"
        in output
    )
    assert "  202603  1 error on 1 of 2 rows: NCF Invalido !" in output


def test_an_error_on_the_header_alone_fails_its_file_with_no_row_named(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = exported(tmp_path, capsys)
    answering(
        monkeypatch,
        [],
        {"control/DGII_F_606_130990026_202603.TXT": HEADER_ONLY},
    )

    main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    output = capsys.readouterr().out
    assert (
        "Labels control, 3 of 4 monthly files with zero errors, "
        "0 of 5 rows with an error" in output
    )
    assert (
        "  202603  1 error on the header, none on its 2 rows: Encabezado:Cantidad "
        "de Registros en el Archivo:(#), NO Coincide con la Cantidad "
        "Calculada:(#)." in output
    )


def test_a_message_quoting_a_value_prints_it_as_hashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form quotes the RNC it rejects; the report never does."""
    export = exported(tmp_path, capsys)
    tipo_id = (
        "status: Proceso Completado\nerrors: 1\nerror box:\n"
        "1. Detalle:Linea: 1: El Rnc: (130990018), NO es válido para el Tipo de "
        "Identificación (2)\r\n\n"
    )
    answering(monkeypatch, [], {"control/DGII_F_606_130990026_202605.TXT": tipo_id})

    main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    output = capsys.readouterr().out
    assert (
        "  202605  1 error on 1 of 1 row: El Rnc: (#########), NO es válido para "
        "el Tipo de Identificación (#)" in output
    )
    assert "130990018" not in output


def test_a_month_with_rows_withheld_counts_them_apart(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A month the export failed keeps its other rows out of the file too;
    they are rows, never pre-validated, so never counted as passing."""
    export = tmp_path / "606"
    (export / "reading").mkdir(parents=True)
    (export / "control").mkdir()
    (export / "export.json").write_text(
        '{"not_reported": [], "control": [], "reading": [{"period": "202603", '
        '"file": null, "rows": 2, "failures": [{"filename": "a.png", '
        '"reasons": ["no NCF"]}]}]}'
    )
    answering(monkeypatch, [])

    main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    output = capsys.readouterr().out
    assert (
        "Reading, 0 of 1 monthly files with zero errors, 1 of 3 rows with an error"
        in output
    )
    assert "  202603  failed by the export, 1 document: no NCF" in output
    assert "  2 rows of failed months withheld by the export, not pre-validated" in (
        output
    )


def test_a_directory_export_606_never_wrote_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answering(monkeypatch, [])

    assert main(prevalidating(tmp_path, tmp_path / "Pre_Validado.zip")) == 1

    assert "export.json does not exist" in capsys.readouterr().err


def test_a_binary_of_another_hash_stops_the_command_before_any_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real runner checks the hash before it builds or runs anything."""
    export = exported(tmp_path, capsys)
    binary = tmp_path / "Pre_Validado.zip"
    binary.write_bytes(b"republished")

    assert main(prevalidating(export, binary)) == 1

    assert f"pinned at {BINARY_SHA256}" in capsys.readouterr().err


ECF_SET = REGIONAL / "ecf" / "manifest.json"
ECF_RUN = REGIONAL / "ecf-run"


def test_prevalidate_reports_the_e_cf_export_for_the_reading_and_the_control(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = tmp_path / "606"
    assert (
        main(
            [
                "export",
                "606",
                "--manifest",
                str(ECF_SET),
                "--run",
                str(ECF_RUN),
                "--out",
                str(export),
            ]
        )
        == 0
    )
    capsys.readouterr()
    asked: list[tuple[str, str, str]] = []
    answering(monkeypatch, asked)

    exit_code = main(prevalidating(export, tmp_path / "Pre_Validado.zip"))

    assert exit_code == 0
    assert capsys.readouterr().out == "\n".join(
        [
            "606 pre-validation",
            f"  export        {export}",
            f"  binary        Pre_Validado.zip sha256 {BINARY_SHA256}",
            "  version       MSI 2.6, exe 4.0.0.9",
            "  runtime       Mono 6.12.0.182, a pass uncompared with one on Windows",
            "  not reported  0 (annotated not reported)",
            "",
            "Reading, 1 of 1 monthly files with zero errors, 0 of 3 rows with an error",
            "  202606  0 errors, 3 rows",
            "",
            "Labels control, 1 of 1 monthly files with zero errors, "
            "0 of 3 rows with an error",
            "  202606  0 errors, 3 rows",
            "",
        ]
    )
    assert asked == [
        ("reading/DGII_F_606_130990026_202606.TXT", "130990026", "202606"),
        ("control/DGII_F_606_130990026_202606.TXT", "130990026", "202606"),
    ]
