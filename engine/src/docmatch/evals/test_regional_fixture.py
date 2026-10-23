"""Tests for the made-up comprobantes the regional fixture is generated from."""

from pathlib import Path

from stdnum.do import cedula, ncf, rnc

from docmatch.evals.manifest import load
from docmatch.evals.public import digest
from docmatch.evals.regional import read_labels
from docmatch.evals.regional_fixture import COMPROBANTES, write_run, write_set
from docmatch.evals.run import read_run_record

FIXTURE = Path(__file__).parents[3] / "tests" / "evals" / "regional"


def test_every_vendor_rnc_is_a_valid_rnc() -> None:
    for each in COMPROBANTES:
        assert rnc.is_valid(each.labels["vendor_tax_id"]), each.filename


def test_every_customer_number_is_a_valid_rnc_or_cedula() -> None:
    for each in COMPROBANTES:
        number = each.labels["customer_tax_id"]
        if number:
            assert rnc.is_valid(number) or cedula.is_valid(number), each.filename


def test_every_ncf_is_a_valid_ncf_or_e_ncf() -> None:
    for each in COMPROBANTES:
        assert ncf.is_valid(each.labels["document_id"]), each.filename


def test_a_consumer_comprobante_names_no_customer() -> None:
    """A B02 is issued to a final consumer, so its customer cell is a labeled
    absence, the case the loader must not read as missing."""
    consumer = [
        each for each in COMPROBANTES if each.labels["document_id"][1:3] == "02"
    ]

    assert consumer
    assert all(each.labels["customer_tax_id"] == "" for each in consumer)


def test_the_set_written_reads_back_as_its_own_labels(tmp_path: Path) -> None:
    write_set(tmp_path)
    manifest = load(tmp_path / "manifest.json")

    labels = read_labels(manifest, tmp_path)

    assert manifest.region == "do"
    assert manifest.document_ids == tuple(each.filename for each in COMPROBANTES)
    assert labels.header(COMPROBANTES[0].filename)["vendor_tax_id"] == (
        COMPROBANTES[0].labels["vendor_tax_id"],
    )


def test_the_manifest_pins_the_digest_of_every_image_written(tmp_path: Path) -> None:
    write_set(tmp_path)
    manifest = load(tmp_path / "manifest.json")

    for filename, pinned in manifest.digests.items():
        assert digest((tmp_path / filename).read_bytes()) == pinned


def test_the_run_written_records_the_region(tmp_path: Path) -> None:
    write_set(tmp_path / "do")
    write_run(tmp_path / "run", load(tmp_path / "do" / "manifest.json"))

    assert read_run_record(tmp_path / "run" / "run.json").region == "do"
    assert load(tmp_path / "run" / "manifest.json").region == "do"


def test_the_committed_labels_and_run_are_what_the_generator_writes(
    tmp_path: Path,
) -> None:
    """Images are left out: their bytes depend on the zlib that wrote them,
    so the committed manifest pins the committed images instead."""
    write_set(tmp_path / "do")
    write_run(tmp_path / "run", load(tmp_path / "do" / "manifest.json"))

    for name in ("do/labels.csv", "run/predictions.json", "run/run.json"):
        assert (FIXTURE / name).read_bytes() == (tmp_path / name).read_bytes(), name


def test_the_committed_manifest_pins_the_committed_images() -> None:
    manifest = load(FIXTURE / "do" / "manifest.json")

    for filename, pinned in manifest.digests.items():
        assert digest((FIXTURE / "do" / filename).read_bytes()) == pinned
    assert manifest.files["labels.csv"] == digest(
        (FIXTURE / "do" / "labels.csv").read_bytes()
    )
