"""Tests for the made-up e-CFs the e-CF fixture is generated from, and for
pinning an e-CF set with labels read from its own XML."""

import hashlib
import os
from pathlib import Path

import pytest
import xmlschema
from stdnum.do import cedula, ncf, rnc

from docmatch.evals.ecf import COVERAGE, SOURCE, SPLIT, ECFError, pin
from docmatch.evals.ecf_fixture import (
    ECF,
    ECFS,
    FIXTURE,
    RUN,
    SET,
    XSD_DIGESTS,
    XSD_TYPOS,
    to_xml,
    write_fixture_set,
    write_run,
)
from docmatch.evals.manifest import load
from docmatch.evals.public import digest
from docmatch.evals.regional import read_labels
from docmatch.evals.regional_fixture import COVERAGE as PAPER_COVERAGE
from docmatch.evals.run import read_run_record
from docmatch.extraction.schema import REGIONAL_FIELDTYPES

XSDS = os.environ.get("DOCMATCH_ECF_XSDS")
"""A directory holding DGII's four XSDs v1.0, which are not committed; CI
downloads them, and the validation tests skip without them."""


def test_the_fixture_has_one_e_cf_of_every_type_the_parser_reads() -> None:
    assert sorted(each.kind for each in ECFS) == [31, 32, 41, 43]


def test_every_party_number_is_a_valid_rnc_or_cedula() -> None:
    for each in ECFS:
        for number in (each.issuer_rnc, each.buyer_rnc):
            if number is not None:
                assert rnc.is_valid(number) or cedula.is_valid(number), each.filename


def test_every_e_ncf_and_modified_ncf_is_valid() -> None:
    for each in ECFS:
        assert ncf.is_valid(each.encf), each.filename
        if each.modified is not None:
            assert ncf.is_valid(each.modified), each.filename


def test_a_regional_fieldtype_is_labeled_present_once_and_absent_once() -> None:
    for fieldtype in REGIONAL_FIELDTYPES:
        cells = [bool(each.labels[fieldtype]) for each in ECFS]
        assert any(cells) and not all(cells), fieldtype


def test_the_e_cf_set_covers_what_the_paper_set_covers() -> None:
    assert COVERAGE == PAPER_COVERAGE


@pytest.mark.skipif(XSDS is None, reason="DOCMATCH_ECF_XSDS names no XSD directory")
@pytest.mark.parametrize("ecf", ECFS, ids=lambda each: each.filename)
def test_every_fixture_e_cf_validates_against_its_xsd(ecf: ECF) -> None:
    assert XSDS is not None
    path = Path(XSDS) / f"e{ecf.kind}.xsd"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == XSD_DIGESTS[ecf.kind]
    schema = xmlschema.XMLSchema(path, validation="lax")

    assert [str(error.message) for error in schema.all_errors] == list(
        XSD_TYPOS.get(ecf.kind, ())
    )
    assert [str(error) for error in schema.iter_errors(to_xml(ecf).decode())] == []


def test_the_set_written_reads_back_as_its_hand_written_labels(
    tmp_path: Path,
) -> None:
    write_fixture_set(tmp_path)
    manifest = load(tmp_path / "manifest.json")

    labels = read_labels(manifest, tmp_path)

    assert manifest.region == "do"
    assert (manifest.split, manifest.source) == (SPLIT, "synthetic")
    assert manifest.document_ids == tuple(each.filename for each in ECFS)
    for each in ECFS:
        assert labels.header(each.filename) == {
            key: (value,) if value else () for key, value in each.labels.items()
        }
        assert labels.paper(each.filename) is None


def test_the_committed_set_and_run_are_what_the_generator_writes(
    tmp_path: Path,
) -> None:
    """XML and CSV are text, so unlike the paper set's images every file is
    compared, the e-CFs included."""
    manifest = write_fixture_set(tmp_path / SET)
    write_run(tmp_path / RUN, manifest)

    for directory in (SET, RUN):
        for path in sorted((tmp_path / directory).iterdir()):
            committed = FIXTURE / directory / path.name
            assert committed.read_bytes() == path.read_bytes(), committed
        assert sorted(each.name for each in (FIXTURE / directory).iterdir()) == sorted(
            each.name for each in (tmp_path / directory).iterdir()
        )


def test_the_run_written_records_the_parser_and_the_region(tmp_path: Path) -> None:
    write_run(tmp_path, write_fixture_set(tmp_path / "set"))

    record = read_run_record(tmp_path / "run.json")

    assert (record.backend, record.region) == ("ecf", "do")


def test_pinning_a_directory_labels_every_e_cf_from_its_own_xml(
    tmp_path: Path,
) -> None:
    for each in ECFS:
        (tmp_path / each.filename).write_bytes(to_xml(each))

    manifest = pin(tmp_path)
    labels = read_labels(manifest, tmp_path)

    assert manifest.source == SOURCE
    assert manifest.coverage == COVERAGE
    for each in ECFS:
        assert manifest.digests[each.filename] == digest(to_xml(each))
        assert labels.header(each.filename)["document_id"] == (each.encf,)


def test_pinning_refuses_a_file_that_is_not_an_e_cf_and_writes_nothing(
    tmp_path: Path,
) -> None:
    (tmp_path / "ecf0001.xml").write_bytes(to_xml(ECFS[0]))
    (tmp_path / "ecf0002.xml").write_bytes(b"<Factura/>")

    with pytest.raises(ECFError, match="ecf0002.xml"):
        pin(tmp_path)

    assert sorted(each.name for each in tmp_path.iterdir()) == [
        "ecf0001.xml",
        "ecf0002.xml",
    ]


def test_pinning_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ECFError, match="no .xml"):
        pin(tmp_path)
