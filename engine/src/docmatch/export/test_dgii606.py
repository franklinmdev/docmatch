"""Tests for the 606 built from a saved run's readings and from the labels.

Every RNC, cedula and NCF here is made up and passes python-stdnum 2.2; the
amounts are made up too and follow no tax rate.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from docmatch.export.dgii606 import NO_MONTH, REGION, Failure, export_606
from docmatch.export.filing import ALL, Filing, Share
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_date, reads_day_first

REPORTER = "130990026"
SUPPLIER = "130990018"
PERSON = "00099000010"


def header(**values: str) -> FieldValues:
    return {fieldtype: (value,) for fieldtype, value in values.items()}


def comprobante(**changes: str) -> FieldValues:
    """A B01 for goods, as a reading or as its labels carry it."""
    values = {
        "vendor_tax_id": SUPPLIER,
        "document_id": "B0100000417",
        "date_issue": "14/03/2026",
        "amount_total_net": "2,500.00",
        "amount_total_tax": "375.00",
        **changes,
    }
    return header(**{key: value for key, value in values.items() if value})


GOODS = Filing(
    reported=True,
    purchase_type=9,
    split="goods",
    payment_date=None,
    payment_method=1,
    itbis_proportional=None,
    itbis_to_cost=None,
    isr_retention_type=None,
)
NOT_REPORTED = Filing(False, None, None, None, None, None, None, None)


def test_a_comprobante_for_goods_is_one_line_of_23_fields_under_the_header() -> None:
    """The shape the pre-validator passed on #217 (`good-v1.txt`), with this
    comprobante's values: CRLF between lines, none after the last."""
    exported = export_606(
        REPORTER, {"a.png": comprobante()}, {"a.png": comprobante()}, {"a.png": GOODS}
    )

    assert exported.reading.files() == {
        "DGII_F_606_130990026_202603.TXT": (
            "606|130990026|202603|1\r\n"
            "130990018|1|09|B0100000417||20260314||0.00|2500.00|2500.00|375.00|"
            "0.00|0.00|0.00|375.00|0.00||0.00|0.00|0.00|0.00|0.00|1"
        )
    }


def test_read_amounts_and_the_filing_annotation_fill_every_other_column() -> None:
    """A mixed E31 to cost, paid by transfer, retentions printed on it, with a
    tip, selective and other taxes: what was read lands in its column, the
    annotation in its own, and code computes the split's goods, the total and
    the ITBIS por adelantar."""
    reading = comprobante(
        vendor_tax_id=PERSON,
        document_id="E310000000052",
        document_id_modified="B0100000417",
        date_issue="2026-05-21",
        amount_total_net="8,000.00",
        amount_total_tax="1,440.00",
        amount_retention_itbis="432.00",
        amount_retention_isr="800.00",
        amount_tax_selective="50.00",
        amount_tax_other="25.00",
        amount_tip="800.00",
    )
    mixed = Filing(
        reported=True,
        purchase_type=2,
        split=Decimal("6000.00"),
        payment_date=date(2026, 5, 30),
        payment_method=2,
        itbis_proportional=ALL,
        itbis_to_cost=Decimal("40.00"),
        isr_retention_type=2,
    )

    exported = export_606(REPORTER, {"a.png": reading}, {}, {"a.png": mixed})

    [content] = exported.reading.files().values()
    assert content.split("\r\n")[1].split("|") == [
        PERSON,
        "2",
        "02",
        "E310000000052",
        "B0100000417",
        "20260521",
        "20260530",
        "6000.00",
        "2000.00",
        "8000.00",
        "1440.00",
        "432.00",
        "1440.00",
        "40.00",
        "1400.00",
        "0.00",
        "02",
        "800.00",
        "0.00",
        "50.00",
        "25.00",
        "800.00",
        "2",
    ]


def test_an_identifier_written_with_hyphens_is_written_as_its_digits() -> None:
    reading = comprobante(vendor_tax_id="1-30-99001-8", document_id="b01 00000417")

    exported = export_606(REPORTER, {"a.png": reading}, {}, {"a.png": GOODS})

    [content] = exported.reading.files().values()
    assert content.split("\r\n")[1].startswith("130990018|1|09|B0100000417|")


def test_each_month_of_the_comprobante_dates_is_its_own_file() -> None:
    readings = {
        "a.png": comprobante(),
        "b.png": comprobante(document_id="B0100000418", date_issue="02/04/2026"),
        "c.png": comprobante(document_id="B0100000419", date_issue="31/03/2026"),
    }
    filing = dict.fromkeys(readings, GOODS)

    files = export_606(REPORTER, readings, {}, filing).reading.files()

    assert sorted(files) == [
        "DGII_F_606_130990026_202603.TXT",
        "DGII_F_606_130990026_202604.TXT",
    ]
    march = files["DGII_F_606_130990026_202603.TXT"].split("\r\n")
    assert march[0] == "606|130990026|202603|2"
    assert [line.split("|")[3] for line in march[1:]] == [
        "B0100000417",
        "B0100000419",
    ]


def test_a_misread_date_moves_the_row_to_another_month_of_the_reading_only() -> None:
    """Each file groups by its own dates, so the misread shows as a difference."""
    exported = export_606(
        REPORTER,
        {"a.png": comprobante(date_issue="14/04/2026")},
        {"a.png": comprobante()},
        {"a.png": GOODS},
    )

    assert list(exported.reading.files()) == ["DGII_F_606_130990026_202604.TXT"]
    assert list(exported.control.files()) == ["DGII_F_606_130990026_202603.TXT"]


def test_the_control_differs_from_the_reading_only_in_what_was_read() -> None:
    misread = comprobante(amount_total_tax="357.00")
    exported = export_606(
        REPORTER, {"a.png": misread}, {"a.png": comprobante()}, {"a.png": GOODS}
    )

    [reading] = exported.reading.files().values()
    [control] = exported.control.files().values()
    assert reading == control.replace("375.00", "357.00")


def test_a_document_not_reported_is_left_out_of_both_files_and_listed() -> None:
    exported = export_606(
        REPORTER,
        {"a.png": comprobante(), "b.png": comprobante(document_id="")},
        {"a.png": comprobante(), "b.png": comprobante(document_id="")},
        {"a.png": GOODS, "b.png": NOT_REPORTED},
    )

    assert exported.not_reported == ("b.png",)
    for side in (exported.reading, exported.control):
        [content] = side.files().values()
        assert content.startswith("606|130990026|202603|1\r\n")
        assert not side.failed


def test_a_document_with_no_reading_fails_the_no_month_row() -> None:
    exported = export_606(REPORTER, {}, {"a.png": comprobante()}, {"a.png": GOODS})

    assert exported.reading.files() == {}
    [month] = exported.reading.months
    assert month.period == NO_MONTH
    assert month.failures == (Failure("a.png", ("no reading",)),)
    assert exported.reading.failed == (month,)
    assert not exported.control.failed


def test_a_reading_without_ncf_or_rnc_fails_its_month_and_writes_no_file() -> None:
    readings = {
        "a.png": comprobante(document_id=""),
        "b.png": comprobante(vendor_tax_id="", document_id="B0100000418"),
        "c.png": comprobante(document_id="B0100000419", date_issue="02/04/2026"),
    }
    filing = dict.fromkeys(readings, GOODS)

    reading = export_606(REPORTER, readings, {}, filing).reading

    assert list(reading.files()) == ["DGII_F_606_130990026_202604.TXT"]
    [march] = reading.failed
    assert march.period == "202603"
    assert march.failures == (
        Failure("a.png", ("no NCF",)),
        Failure("b.png", ("no RNC",)),
    )


def test_a_reading_without_a_date_goes_to_the_no_month_row() -> None:
    readings = {
        "a.png": comprobante(date_issue=""),
        "b.png": comprobante(document_id="", date_issue="2026-13-40"),
    }
    filing = dict.fromkeys(readings, GOODS)

    reading = export_606(REPORTER, readings, {}, filing).reading

    [month] = reading.months
    assert month.period == NO_MONTH
    assert month.failures == (
        Failure("a.png", ("no date",)),
        Failure("b.png", ("no NCF", "date unreadable")),
    )


def test_an_unreadable_amount_fails_rather_than_being_written_as_zero() -> None:
    """No reason quotes the value, so a report of the owner's comprobantes
    shows none."""
    reading = comprobante(amount_total_tax="ITBIS")

    [march] = export_606(
        REPORTER, {"a.png": reading}, {}, {"a.png": GOODS}
    ).reading.failed

    assert march.failures == (Failure("a.png", ("amount_total_tax unreadable",)),)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"vendor_tax_id": "13099001"}, "RNC or cedula invalid"),
        ({"vendor_tax_id": "130990019"}, "RNC or cedula invalid"),
        ({"vendor_tax_id": "13099|0018"}, "RNC or cedula invalid"),
        ({"vendor_tax_id": "13099001ñ"}, "RNC or cedula invalid"),
        ({"vendor_tax_id": "00099000011"}, "RNC or cedula invalid"),
        ({"document_id": "B01000004170"}, "NCF invalid"),
        ({"document_id": "B01|00000417"}, "NCF invalid"),
        ({"document_id": "B01000004ñ7"}, "NCF invalid"),
        ({"document_id_modified": "B0100000|17"}, "modified NCF invalid"),
    ],
)
def test_an_identifier_stdnum_refuses_fails_its_month(
    changes: dict[str, str], reason: str
) -> None:
    """Only a number python-stdnum 2.2 validates reaches the file, which keeps
    a pipe, a character the file cannot hold, a wrong length or a wrong check
    digit out of it; Tipo Id is then the validator's, RNC 1 and cedula 2."""
    reading = comprobante(**changes)

    [march] = export_606(
        REPORTER, {"a.png": reading}, {}, {"a.png": GOODS}
    ).reading.failed

    assert march.failures == (Failure("a.png", (reason,)),)


def with_shares(proportional: Share, to_cost: Share) -> Filing:
    return replace(GOODS, itbis_proportional=proportional, itbis_to_cost=to_cost)


@pytest.mark.parametrize(
    ("changes", "filing", "reason"),
    [
        ({"amount_total_net": "-2,500.00"}, GOODS, "amount_total_net negative"),
        ({"amount_tip": "-1.00"}, GOODS, "amount_tip negative"),
        (
            {"amount_retention_itbis": "375.01"},
            GOODS,
            "ITBIS retention above the ITBIS",
        ),
        (
            {},
            with_shares(Decimal("375.01"), None),
            "ITBIS to proportionality above the ITBIS",
        ),
        (
            {},
            with_shares(None, Decimal("375.01")),
            "ITBIS to cost above the ITBIS",
        ),
    ],
)
def test_no_column_is_written_below_zero_or_beyond_the_itbis(
    changes: dict[str, str], filing: Filing, reason: str
) -> None:
    """The instructivo (February 2026) names no negative convention, a nota
    de credito included, so a negative or out-of-bounds amount fails rather
    than being written. ITBIS to cost above the ITBIS is also what would
    turn column 15 negative."""
    [march] = export_606(
        REPORTER, {"a.png": comprobante(**changes)}, {}, {"a.png": filing}
    ).reading.failed

    assert march.failures == (Failure("a.png", (reason,)),)


def test_a_share_equal_to_the_itbis_is_written() -> None:
    filing = with_shares(Decimal("375.00"), ALL)

    exported = export_606(REPORTER, {"a.png": comprobante()}, {}, {"a.png": filing})

    [content] = exported.reading.files().values()
    assert content.split("\r\n")[1].split("|")[12:15] == ["375.00", "375.00", "0.00"]


def test_a_comprobante_date_reads_day_first() -> None:
    """Dominican comprobantes print DD/MM/YYYY, so 02/04/2026 is April: the
    export reads its region's order, the rule the gate and the scorer read."""
    day_first = reads_day_first(REGION)
    assert read_date("02/04/2026", day_first=day_first) == date(2026, 4, 2)
    assert read_date("2026-04-02", day_first=day_first) == date(2026, 4, 2)
    assert read_date("02/04/26", day_first=day_first) == date(2026, 4, 2)
    assert read_date("31/02/2026", day_first=day_first) is None


def test_the_header_names_the_reporter_the_month_and_the_rows() -> None:
    exported = export_606(REPORTER, {"a.png": comprobante()}, {}, {"a.png": GOODS})

    [content] = exported.reading.files().values()
    assert content.split("\r\n")[0] == "606|130990026|202603|1"


def test_an_absent_amount_is_written_as_zero() -> None:
    """An empty label or a null reading says the comprobante carries none."""
    reading = comprobante(amount_total_tax="")

    exported = export_606(REPORTER, {"a.png": reading}, {}, {"a.png": GOODS})

    [content] = exported.reading.files().values()
    assert content.split("\r\n")[1].split("|")[10] == "0.00"


def test_a_services_amount_above_the_read_net_fails_rather_than_goods_below_zero() -> (
    None
):
    """A misread net can fall below the annotated services amount; a negative
    goods column would hide it."""
    mixed = Filing(
        reported=True,
        purchase_type=2,
        split=Decimal("3000.00"),
        payment_date=None,
        payment_method=1,
        itbis_proportional=None,
        itbis_to_cost=None,
        isr_retention_type=None,
    )

    [march] = export_606(
        REPORTER, {"a.png": comprobante()}, {}, {"a.png": mixed}
    ).reading.failed

    assert march.failures == (
        Failure("a.png", ("services split above the net amount",)),
    )
