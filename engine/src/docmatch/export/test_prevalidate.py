"""Tests for pre-validating an export's 606 files (#229).

No test runs the container. The harness outputs below were captured from
DGII's pre-validator (MSI 2.6) run under Mono on made-up 606 files; the
command is driven with a runner that answers from them.
"""

import hashlib
from pathlib import Path

import pytest

from docmatch.export.prevalidate import (
    BINARY_SHA256,
    PrevalidateError,
    check_binary,
    read_verdict,
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
