"""Tests for pre-validating an export's 606 files (#229).

No test runs the container. The harness outputs below were captured from
DGII's pre-validator (MSI 2.6) run under Mono on made-up 606 files; the
command is driven with a runner that answers from them.
"""

import hashlib
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from docmatch.export.dgii606 import EXPORT_FILE
from docmatch.export.prevalidate import (
    BINARY_SHA256,
    PrevalidateError,
    check_binary,
    prevalidate,
    read_verdict,
    run_in_container,
)


def test_the_pinned_binary_passes_its_hash_check(tmp_path: Path) -> None:
    """A stand-in file whose hash the test pins in place of DGII's zip."""
    binary = tmp_path / "Pre_Validado.zip"
    binary.write_bytes(b"made up")

    check_binary(binary, hashlib.sha256(b"made up").hexdigest())


def test_a_binary_of_another_hash_fails_loudly(tmp_path: Path) -> None:
    binary = tmp_path / "Pre_Validado.zip"
    binary.write_bytes(b"republished")

    with pytest.raises(PrevalidateError, match=BINARY_SHA256):
        check_binary(binary)


def test_a_missing_binary_says_where_it_was_looked_for(tmp_path: Path) -> None:
    with pytest.raises(PrevalidateError, match="Pre_Validado.zip"):
        check_binary(tmp_path / "Pre_Validado.zip")


# Captured from the harness, byte for byte but for the made-up values: a
# pass, and failures on a line, on a header and on both. The form ends each
# error line with CRLF inside the box, the harness each of its own with LF.

PASSED = "status: Proceso Completado\nerrors: \nerror box:\n\n"

NCF_ON_LINE_2 = (
    "status: Proceso Completado\n"
    "errors: 1\n"
    "error box:\n"
    "1. Detalle:Linea: 2:NCF Invalido !\r\n"
    "\n"
)

LINE_AND_HEADER = (
    "status: Proceso Completado\n"
    "errors: 3\n"
    "error box:\n"
    "1. Detalle:Linea:1 Si tiene ITBIS Retenido debe Especificar la Fecha de Pago\r\n"
    "2. Detalle:Linea:1 El ITBIS Retenido no puede ser Mayor que el ITBIS "
    "Facturado \r\n"
    "3. Encabezado:Cantidad de Registros en el Archivo:(2), NO Coincide con la "
    "Cantidad Calculada:(1).\r\n"
    "\n"
)


def test_a_blank_error_count_and_an_empty_box_is_a_pass() -> None:
    verdict = read_verdict(PASSED)

    assert verdict.passed
    assert verdict.rows() == frozenset()


def test_an_error_names_the_detail_line_it_was_found_on() -> None:
    verdict = read_verdict(NCF_ON_LINE_2)

    assert not verdict.passed
    assert verdict.errors == ("Detalle:Linea: 2:NCF Invalido !",)
    assert verdict.rows() == {2}


def test_an_error_on_the_header_fails_the_file_and_names_no_row() -> None:
    verdict = read_verdict(LINE_AND_HEADER)

    assert len(verdict.errors) == 3
    assert verdict.rows() == {1}


def test_proceso_completado_alone_is_not_a_pass() -> None:
    """The status reads the same on a failure; only the count decides."""
    assert "Proceso Completado" in LINE_AND_HEADER
    assert not read_verdict(LINE_AND_HEADER).passed


def test_a_form_that_never_finished_has_no_verdict() -> None:
    with pytest.raises(PrevalidateError, match="did not finish"):
        read_verdict("status: Procesando Registros\nerrors: \nerror box:\n\n")


def test_a_count_the_box_does_not_bear_out_is_refused() -> None:
    """A count of 2 over one listed error means the harness no longer reads
    the form it was written against."""
    with pytest.raises(PrevalidateError, match="counts 2 errors and lists 1"):
        read_verdict(NCF_ON_LINE_2.replace("errors: 1", "errors: 2"))


def test_output_with_no_verdict_block_is_refused() -> None:
    with pytest.raises(PrevalidateError, match="no 'status' line"):
        read_verdict("Unhandled Exception:\n")


@pytest.mark.parametrize(
    "partial",
    [
        "status: Proceso Completado\nerrors: 1\n",
        "status: Proceso Completado\n",
        "",
    ],
    ids=["no error box", "no count", "nothing"],
)
def test_a_block_cut_short_is_refused_as_the_command_s_own_error(partial: str) -> None:
    """A harness that dies partway through its block leaves no verdict, and
    the command says so rather than raising a traceback."""
    with pytest.raises(PrevalidateError):
        read_verdict(partial)


HEADER_ONLY = (
    "status: Proceso Completado\n"
    "errors: 1\n"
    "error box:\n"
    "1. Encabezado:Cantidad de Registros en el Archivo:(3), NO Coincide con la "
    "Cantidad Calculada:(2).\r\n"
    "\n"
)


def test_an_error_on_the_header_alone_fails_the_file_and_names_no_row() -> None:
    verdict = read_verdict(HEADER_ONLY)

    assert not verdict.passed
    assert verdict.rows() == frozenset()


# The export a command reads, made up and written by hand.


def export_with(tmp_path: Path, months: list[dict[str, object]]) -> Path:
    export = tmp_path / "606"
    (export / "reading").mkdir(parents=True)
    (export / EXPORT_FILE).write_text(
        json.dumps({"not_reported": [], "control": [], "reading": months})
    )
    return export


def never(path: Path, reporter: str, period: str) -> str:
    raise AssertionError(f"{path} was run")


def test_a_failed_month_counts_its_failures_with_an_error_and_withholds_the_rest(
    tmp_path: Path,
) -> None:
    export = export_with(
        tmp_path,
        [
            {
                "period": "202603",
                "file": None,
                "rows": 2,
                "failures": [{"filename": "a.png", "reasons": ["no NCF"]}],
            }
        ],
    )

    (reading, _) = prevalidate(export, never).sides

    assert (reading.passed, reading.rows, reading.rows_with_error) == (0, 3, 1)
    assert reading.withheld == 2


@pytest.mark.parametrize(
    "file",
    ["../DGII_F_606_130990026_202603.TXT", "/tmp/DGII_F_606_130990026_202603.TXT"],
    ids=["outside", "absolute"],
)
def test_a_file_outside_the_export_is_refused(tmp_path: Path, file: str) -> None:
    export = export_with(
        tmp_path, [{"period": "202603", "file": file, "rows": 1, "failures": []}]
    )

    with pytest.raises(PrevalidateError, match="inside"):
        prevalidate(export, never)


@pytest.mark.parametrize("rows", [True, -1], ids=["bool", "negative"])
def test_a_row_count_that_is_not_a_count_is_refused(
    tmp_path: Path, rows: object
) -> None:
    export = export_with(
        tmp_path, [{"period": "202603", "file": None, "rows": rows, "failures": []}]
    )

    with pytest.raises(PrevalidateError, match="badly"):
        prevalidate(export, never)


# One file's run in the container, the docker CLI faked.


class Docker:
    """Stands in for `subprocess.run`: records each docker command and
    answers from what the test gives it."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[list[str]] = []

    def __call__(self, command: Sequence[str], **options: object) -> object:
        self.calls.append(list(command))
        if command[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: object
) -> tuple[Docker, Path]:
    docker = Docker(answer)
    monkeypatch.setattr(subprocess, "run", docker)
    file = tmp_path / "out" / "DGII_F_606_130990026_202603.TXT"
    file.parent.mkdir()
    file.write_text("606|130990026|202603|0\r\n")
    return docker, file


def name_of(call: list[str]) -> str:
    return call[call.index("--name") + 1]


def test_a_run_that_times_out_removes_its_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The timeout kills the docker CLI, never the container it started."""
    docker, file = running(
        monkeypatch, tmp_path, subprocess.TimeoutExpired("docker", 300)
    )

    with pytest.raises(PrevalidateError, match="took over 300 s"):
        run_in_container(tmp_path / "Pre_Validado.zip", "image", file, "1", "2")

    (run, remove) = docker.calls
    assert remove == ["docker", "rm", "-f", name_of(run)]


def test_an_interrupted_run_removes_its_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docker, file = running(monkeypatch, tmp_path, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_in_container(tmp_path / "Pre_Validado.zip", "image", file, "1", "2")

    (run, remove) = docker.calls
    assert remove == ["docker", "rm", "-f", name_of(run)]


def test_each_run_has_its_own_container_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    done = subprocess.CompletedProcess([], 0, PASSED.encode(), b"")
    docker, file = running(monkeypatch, tmp_path, done)

    for _ in range(2):
        run_in_container(tmp_path / "Pre_Validado.zip", "image", file, "1", "2")

    assert len({name_of(call) for call in docker.calls}) == 2


def test_the_zip_and_the_file_are_bind_mounted_read_only_with_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    done = subprocess.CompletedProcess([], 0, PASSED.encode(), b"")
    docker, file = running(monkeypatch, tmp_path, done)
    binary = tmp_path / "Pre_Validado.zip"

    run_in_container(binary, "image", file, "1", "2")

    (call,) = docker.calls
    assert call[call.index("--network") + 1] == "none"
    assert [call[at + 1] for at, word in enumerate(call) if word == "--mount"] == [
        f"type=bind,source={binary.resolve()},target=/dgii/Pre_Validado.zip,readonly",
        f"type=bind,source={file.parent.resolve()},target=/files,readonly",
    ]


def test_a_path_the_mount_syntax_cannot_hold_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docker, file = running(monkeypatch, tmp_path, AssertionError("ran"))

    with pytest.raises(PrevalidateError, match="comma"):
        run_in_container(tmp_path / "a,b.zip", "image", file, "1", "2")

    assert docker.calls == []


def test_the_harness_output_is_read_as_utf_8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness writes UTF-8 whatever the host's locale, and the form's
    messages carry accents."""
    printed = "status: Proceso Completado\nerrors: \nerror box:\nválido\n"
    done = subprocess.CompletedProcess([], 0, printed.encode("utf-8"), b"")
    _, file = running(monkeypatch, tmp_path, done)

    output = run_in_container(tmp_path / "Pre_Validado.zip", "image", file, "1", "2")

    assert output == printed


def test_output_that_is_not_utf_8_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    done = subprocess.CompletedProcess([], 0, "válido".encode("latin-1"), b"")
    _, file = running(monkeypatch, tmp_path, done)

    with pytest.raises(PrevalidateError, match="UTF-8"):
        run_in_container(tmp_path / "Pre_Validado.zip", "image", file, "1", "2")


def test_ci_downloads_and_caches_the_binary_this_command_pins() -> None:
    """The pin lives in the code and in CI's cache key and check; a bump of
    one without the other would leave CI caching a binary the command
    refuses, or checking a hash the command no longer runs."""
    workflow = Path(__file__).parents[4] / ".github" / "workflows" / "ci.yml"

    assert workflow.read_text().count(BINARY_SHA256) == 2
