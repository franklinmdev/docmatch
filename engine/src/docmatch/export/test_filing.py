"""Tests for reading a regional set's filing annotation from its `filing.csv`."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.evals.manifest import Manifest
from docmatch.evals.public import digest
from docmatch.evals.regional import RegionalError
from docmatch.export.filing import ALL, FILING_FILE, Filing, read_filing

HEADER = (
    "filename,reported,purchase_type,split,payment_date,payment_method,"
    "itbis_proportional,itbis_to_cost,isr_retention_type"
)


def pinned(directory: Path, *rows: str, header: str = HEADER) -> Manifest:
    """A two-document set whose `filing.csv` holds `rows`, pinned as written."""
    content = "\n".join((header, *rows)).encode() + b"\n"
    (directory / FILING_FILE).write_bytes(content)
    return Manifest(
        split="paper",
        seed=0,
        source="synthetic",
        size=2,
        document_ids=("do0001.png", "do0002.png"),
        region="do",
        coverage=("document_id",),
        files={FILING_FILE: digest(content)},
        reporter="130990026",
    )


GOODS = "do0001.png,yes,09,goods,,1,,,"
NOT_REPORTED = "do0002.png,no,,,,,,,"


def test_a_reported_document_reads_as_its_choices(tmp_path: Path) -> None:
    manifest = pinned(
        tmp_path,
        "do0001.png,yes,2,1500.00,2026-05-30,2,all,25.50,02",
        NOT_REPORTED,
    )

    filing = read_filing(manifest, tmp_path)

    assert filing["do0001.png"] == Filing(
        reported=True,
        purchase_type=2,
        split=Decimal("1500.00"),
        payment_date=date(2026, 5, 30),
        payment_method=2,
        itbis_proportional=ALL,
        itbis_to_cost=Decimal("25.50"),
        isr_retention_type=2,
    )


def test_empty_choices_are_none_and_a_whole_split_is_named(tmp_path: Path) -> None:
    filing = read_filing(pinned(tmp_path, GOODS, NOT_REPORTED), tmp_path)

    assert filing["do0001.png"] == Filing(
        reported=True,
        purchase_type=9,
        split="goods",
        payment_date=None,
        payment_method=1,
        itbis_proportional=None,
        itbis_to_cost=None,
        isr_retention_type=None,
    )


def test_a_document_not_reported_needs_no_other_choice(tmp_path: Path) -> None:
    filing = read_filing(pinned(tmp_path, GOODS, NOT_REPORTED), tmp_path)

    assert filing["do0002.png"].reported is False


def test_an_unknown_column_is_refused(tmp_path: Path) -> None:
    manifest = pinned(
        tmp_path, GOODS + ",x", NOT_REPORTED + ",", header=HEADER + ",ncf_type"
    )

    with pytest.raises(RegionalError, match="ncf_type"):
        read_filing(manifest, tmp_path)


def test_a_missing_column_is_refused(tmp_path: Path) -> None:
    header = HEADER.removesuffix(",isr_retention_type")
    manifest = pinned(
        tmp_path, GOODS.removesuffix(","), NOT_REPORTED[:-1], header=header
    )

    with pytest.raises(RegionalError, match="isr_retention_type"):
        read_filing(manifest, tmp_path)


def test_a_document_annotated_twice_is_refused(tmp_path: Path) -> None:
    manifest = pinned(tmp_path, GOODS, NOT_REPORTED, GOODS)

    with pytest.raises(RegionalError, match="do0001.png"):
        read_filing(manifest, tmp_path)


def test_a_file_changed_after_it_was_pinned_is_refused(tmp_path: Path) -> None:
    manifest = pinned(tmp_path, GOODS, NOT_REPORTED)
    (tmp_path / FILING_FILE).write_text(f"{HEADER}\n{NOT_REPORTED}\n{GOODS}\n")

    with pytest.raises(RegionalError, match="digest"):
        read_filing(manifest, tmp_path)


def test_a_pinned_document_with_no_annotation_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegionalError, match="do0002.png"):
        read_filing(pinned(tmp_path, GOODS), tmp_path)


@pytest.mark.parametrize(
    ("row", "named"),
    [
        ("do0001.png,maybe,09,goods,,1,,,", "reported"),
        ("do0001.png,yes,12,goods,,1,,,", "purchase_type"),
        ("do0001.png,yes,,goods,,1,,,", "purchase_type"),
        ("do0001.png,yes,09,both,,1,,,", "split"),
        ("do0001.png,yes,09,,,1,,,", "split"),
        ("do0001.png,yes,09,goods,30/05/2026,1,,,", "payment_date"),
        ("do0001.png,yes,09,goods,,8,,,", "payment_method"),
        ("do0001.png,yes,09,goods,,,,,", "payment_method"),
        ("do0001.png,yes,09,goods,,1,some,,", "itbis_proportional"),
        ("do0001.png,yes,09,goods,,1,,-1.00,", "itbis_to_cost"),
        ("do0001.png,yes,09,goods,,1,,,10", "isr_retention_type"),
    ],
)
def test_a_choice_outside_what_the_606_allows_is_refused(
    tmp_path: Path, row: str, named: str
) -> None:
    """The codes are the instructivo's (DGII, February 2026): purchase types 1
    to 11, payment methods 1 to 7, ISR retention types 1 to 9."""
    manifest = pinned(tmp_path, row, NOT_REPORTED)

    with pytest.raises(RegionalError, match=f"do0001.png.*{named}"):
        read_filing(manifest, tmp_path)


def test_a_manifest_pinning_no_filing_annotation_is_refused(tmp_path: Path) -> None:
    manifest = pinned(tmp_path, GOODS, NOT_REPORTED).model_copy(update={"files": {}})

    with pytest.raises(RegionalError, match=FILING_FILE):
        read_filing(manifest, tmp_path)


TYPE_HEADER = HEADER.replace("filename", "ecf_type")
E31 = "E31,yes,09,goods,,1,,,"
E41 = "E41,yes,02,services,2026-06-20,2,,,2"


def an_ecf_set(directory: Path, *rows: str, declared: bool = True) -> Manifest:
    """Three e-CFs, two E31 and one E41, whose `filing.csv` holds `rows` by
    type; the labels name the type through the e-NCF."""
    ids = ("ecf0001.xml", "ecf0002.xml", "ecf0003.xml")
    labels = (
        b"filename,document_id\n"
        b"ecf0001.xml,E310000000001\n"
        b"ecf0002.xml,E310000000002\n"
        b"ecf0003.xml,E410000000001\n"
    )
    filing = "\n".join((TYPE_HEADER, *rows)).encode() + b"\n"
    (directory / "labels.csv").write_bytes(labels)
    (directory / FILING_FILE).write_bytes(filing)
    return Manifest(
        split="ecf",
        seed=0,
        source="synthetic",
        size=3,
        document_ids=ids,
        region="do",
        coverage=("document_id",),
        files={"labels.csv": digest(labels), FILING_FILE: digest(filing)},
        reporter="130990026",
        filing_per="ecf_type" if declared else None,
    )


def test_a_set_declaring_it_annotates_once_per_type_for_every_document_of_it(
    tmp_path: Path,
) -> None:
    filing = read_filing(an_ecf_set(tmp_path, E31, E41), tmp_path)

    assert filing["ecf0001.xml"] == filing["ecf0002.xml"]
    assert filing["ecf0001.xml"].purchase_type == 9
    assert filing["ecf0003.xml"].purchase_type == 2
    assert filing["ecf0003.xml"].isr_retention_type == 2


def test_a_set_not_declaring_it_keeps_one_row_per_document(tmp_path: Path) -> None:
    manifest = an_ecf_set(tmp_path, E31, E41, declared=False)

    with pytest.raises(RegionalError, match="filename"):
        read_filing(manifest, tmp_path)


def test_a_type_with_documents_and_no_row_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegionalError, match="E41"):
        read_filing(an_ecf_set(tmp_path, E31), tmp_path)


def test_a_row_for_a_type_no_document_has_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegionalError, match="E43"):
        read_filing(an_ecf_set(tmp_path, E31, E41, "E43,no,,,,,,,"), tmp_path)


def test_a_type_annotated_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RegionalError, match="E31"):
        read_filing(an_ecf_set(tmp_path, E31, E41, E31), tmp_path)


def test_a_label_that_names_no_e_cf_type_is_refused(tmp_path: Path) -> None:
    manifest = an_ecf_set(tmp_path, E31, E41)
    labels = (
        b"filename,document_id\n"
        b"ecf0001.xml,B0100000001\n"
        b"ecf0002.xml,E310000000002\n"
        b"ecf0003.xml,E410000000001\n"
    )
    (tmp_path / "labels.csv").write_bytes(labels)
    manifest = manifest.model_copy(
        update={"files": {**manifest.files, "labels.csv": digest(labels)}}
    )

    with pytest.raises(RegionalError, match="ecf0001.xml.*e-CF type"):
        read_filing(manifest, tmp_path)
