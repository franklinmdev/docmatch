"""Tests for the e-CF parser backend, over the made-up e-CFs the fixture
generator writes from DGII's XSDs v1.0 (spec #222, seam 2)."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.evals.ecf import MODEL, ECFError, ECFExtractor, read
from docmatch.evals.ecf_fixture import ECF, ECFS, to_xml
from docmatch.extraction.extractor import Document, ExtractionError


def by_type(kind: int) -> ECF:
    return next(each for each in ECFS if each.kind == kind)


def test_an_e31_reads_into_every_covered_fieldtype_it_carries() -> None:
    e31 = by_type(31)

    prediction = read(to_xml(e31))

    assert prediction.fields == {
        "document_id": "E310000000001",
        "date_issue": "2026-06-10",
        "vendor_name": "Distribuidora Ficticia del Cibao SRL",
        "vendor_tax_id": "130990052",
        "customer_tax_id": "130990069",
        "amount_total_net": "10500.00",
        "amount_total_tax": "1800.00",
        "amount_total_gross": "13650.00",
        "amount_tip": "1000.00",
        "amount_tax_selective": "150.00",
        "amount_tax_other": "200.00",
        "document_id_modified": "B0100000005",
        "amount_retention_itbis": None,
        "amount_retention_isr": None,
    }


@pytest.mark.parametrize("ecf", ECFS, ids=lambda each: each.filename)
def test_every_fixture_e_cf_reads_as_its_hand_written_labels(ecf: ECF) -> None:
    prediction = read(to_xml(ecf))
    labeled = {key: value or None for key, value in ecf.labels.items()}

    assert prediction.fields == labeled


def test_an_e32_with_an_empty_buyer_and_no_exempt_amount_reads_them_absent() -> None:
    fields = read(to_xml(by_type(32))).fields

    assert fields["customer_tax_id"] is None
    assert fields["amount_total_net"] == "1000.00"


def test_an_e41_names_its_buyer_issuer_the_customer_and_its_seller_the_vendor() -> None:
    e41 = by_type(41)

    fields = read(to_xml(e41)).fields

    assert fields["vendor_tax_id"] == e41.buyer_rnc
    assert fields["vendor_name"] == e41.buyer_name
    assert fields["customer_tax_id"] == e41.issuer_rnc


def test_an_e41_with_retentions_on_its_lines_only_reads_their_sum() -> None:
    fields = read(to_xml(by_type(41))).fields

    assert fields["amount_retention_itbis"] == "900.00"
    assert fields["amount_retention_isr"] == "500.00"


def test_a_printed_header_retention_is_read_over_the_lines_sum() -> None:
    printed = replace(by_type(41), retained_itbis="1000.00", retained_isr="600.00")

    fields = read(to_xml(printed)).fields

    assert fields["amount_retention_itbis"] == "1000.00"
    assert fields["amount_retention_isr"] == "600.00"


def test_an_e43_names_no_vendor() -> None:
    fields = read(to_xml(by_type(43))).fields

    assert fields["vendor_name"] is None
    assert fields["vendor_tax_id"] is None
    assert fields["customer_tax_id"] == by_type(43).issuer_rnc


def test_the_parser_reads_no_line_item() -> None:
    assert read(to_xml(by_type(31))).line_items == ()


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"<ECF><Encabezado>", "not well-formed"),
        (b"<Factura/>", "root element is Factura"),
        (
            to_xml(by_type(31)).replace(b"<TipoeCF>31<", b"<TipoeCF>33<"),
            "TipoeCF 33",
        ),
        (
            to_xml(by_type(31)).replace(b">10-06-2026<", b">2026-06-10<"),
            "not dd-MM-yyyy",
        ),
    ],
)
def test_content_that_is_not_a_readable_e_cf_is_refused(
    content: bytes, reason: str
) -> None:
    with pytest.raises(ECFError, match=reason):
        read(content)


def test_the_backend_reads_a_file_at_no_cost_and_no_served_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ecf0001.xml"
    path.write_bytes(to_xml(by_type(31)))

    extraction = ECFExtractor().extract(Document("ecf0001.xml", path, pages=1))

    assert extraction.prediction == read(to_xml(by_type(31)))
    assert extraction.cost == Decimal(0)
    assert extraction.usage.total_tokens == 0 and extraction.usage.pages == 0
    assert extraction.served_model is None
    assert ECFExtractor().model == MODEL


def test_the_backend_refuses_a_file_that_is_not_an_e_cf_without_a_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ecf0001.xml"
    path.write_bytes(b"<Factura/>")

    with pytest.raises(ExtractionError) as raised:
        ECFExtractor().extract(Document("ecf0001.xml", path, pages=1))

    assert not raised.value.retryable
    assert raised.value.cost == Decimal(0)
