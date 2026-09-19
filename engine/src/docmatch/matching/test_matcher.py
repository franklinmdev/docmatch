"""Tests for the matcher on hand-built records: seam 1 of the Phase 2 spec.

What the matcher is worth against the generator's truth is pinned in
`test_scoring.py` and over the synthetic fixture; these pin what one match
result says about one case. Every value is made up.
"""

from collections.abc import Sequence
from decimal import Decimal

import pytest

from docmatch.matching.matcher import (
    Candidate,
    Finding,
    MatchResult,
    Pairing,
    Place,
    Unpaired,
    explain,
    match,
)
from docmatch.matching.records import ReceiptLine, ReceivingRecord, Record
from docmatch.matching.tolerances import PRICE, QUANTITY

FieldValuesDict = dict[str, tuple[str, ...]]


def line(**cells: str | Sequence[str]) -> FieldValuesDict:
    return {
        f"line_item_{name}": (value,) if isinstance(value, str) else tuple(value)
        for name, value in cells.items()
    }


def record(*lines: FieldValuesDict, **header: str) -> Record:
    return Record(
        header={fieldtype: (value,) for fieldtype, value in header.items()},
        lines=lines,
    )


NO_RECEIPT = ReceivingRecord(lines=())


def matched(invoice: Record, purchase_order: Record) -> MatchResult:
    return match(invoice, purchase_order, NO_RECEIPT)


def one_line(
    invoice_price: str | Sequence[str], po_price: str | Sequence[str]
) -> MatchResult:
    """The #70 worked example's shape: one line, compared on its unit price."""
    return matched(
        record(line(description="Torque wrench", unit_price_gross=invoice_price)),
        record(line(description="Torque wrench", unit_price_gross=po_price)),
    )


# The #70 worked example: PO 35.30, 1 percent of it is 0.353, above a cent.


@pytest.mark.parametrize("invoice_price", ["35.31", "35.65", "35.30"])
def test_a_unit_price_within_the_tolerance_is_not_a_finding(
    invoice_price: str,
) -> None:
    assert one_line(invoice_price, "35.30").findings == ()


def test_a_unit_price_past_the_tolerance_is_a_price_variance_on_the_po_line() -> None:
    result = one_line("35.66", "35.30")

    assert result.findings == (
        Finding(
            type="price variance",
            place=Place("po line", 0),
            cell="unit price",
            invoice=("35.66",),
            purchase_order=("35.30",),
            margin=Decimal("0.353"),
            tolerance=PRICE,
        ),
    )


def test_a_finding_explains_itself_with_the_values_and_the_constant() -> None:
    (finding,) = one_line("35.66", "35.30").findings

    assert explain(finding) == (
        'price variance, PO line 0, unit price, invoice "35.66" vs PO "35.30", '
        "margin 0.353 (1 percent of 35.30, above 0.01)"
    )


def test_billing_below_the_purchase_order_never_fires() -> None:
    assert one_line("20.00", "35.30").findings == ()


def test_the_cent_floor_holds_on_values_under_one() -> None:
    """1 percent of 0.50 is half a cent, so the cent is what the overage must clear."""
    assert one_line("0.51", "0.50").findings == ()
    assert [each.cell for each in one_line("0.52", "0.50").findings] == ["unit price"]


def test_price_variance_falls_to_the_amount_when_either_side_lacks_a_unit_price() -> (
    None
):
    result = matched(
        record(line(description="Hex key set", amount_gross="46.00")),
        record(
            line(
                description="Hex key set",
                unit_price_gross="15.00",
                amount_gross="45.00",
            )
        ),
    )

    assert [
        (each.cell, each.invoice, each.purchase_order) for each in result.findings
    ] == [("amount", ("46.00",), ("45.00",))]
    assert [(each.cell, each.reason) for each in result.not_compared] == [
        ("unit price", "absent")
    ]


def test_price_variance_falls_to_the_amount_when_a_unit_price_is_unreadable() -> None:
    result = matched(
        record(
            line(
                description="Hex key set", unit_price_gross="n/a", amount_gross="46.00"
            )
        ),
        record(
            line(
                description="Hex key set",
                unit_price_gross="15.00",
                amount_gross="45.00",
            )
        ),
    )

    assert [each.cell for each in result.findings] == ["amount"]
    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("unit price", ("n/a",), "unreadable")
    ]


def test_a_cell_one_side_lacks_is_listed_once_whatever_the_other_side_says() -> None:
    """Absent on one side is the reason, not the other side's unreadable text."""
    result = matched(
        record(line(description="Hex key set", amount_gross="46.00")),
        record(
            line(
                description="Hex key set", unit_price_gross="n/a", amount_gross="45.00"
            )
        ),
    )

    assert [(each.cell, each.reason) for each in result.not_compared] == [
        ("unit price", "absent")
    ]
    assert [each.cell for each in result.findings] == ["amount"]


def test_a_net_column_is_compared_when_there_is_no_gross_one() -> None:
    result = matched(
        record(line(description="Hex key set", unit_price_net="46.00")),
        record(line(description="Hex key set", unit_price_gross="45.00")),
    )

    assert [each.cell for each in result.findings] == ["unit price"]


def test_no_finding_when_nothing_on_the_line_is_comparable() -> None:
    result = matched(
        record(line(description="Hex key set", quantity="3")),
        record(line(description="Hex key set", quantity="3")),
    )

    assert result.findings == ()
    assert result.not_compared == ()
    assert result.verdict == "approvable"


def test_a_comparison_within_the_tolerance_lists_nothing_as_not_compared() -> None:
    assert one_line("35.31", "35.30").not_compared == ()


def test_listed_values_fire_on_the_combination_least_favourable_to_the_buyer() -> None:
    """The highest invoice value against the lowest PO value, as the gate does."""
    invoice_disagrees = one_line(["35.30", "35.66"], "35.30")
    assert [each.invoice for each in invoice_disagrees.findings] == [("35.30", "35.66")]

    po_disagrees = one_line("35.66", ["36.00", "35.30"])
    assert [each.purchase_order for each in po_disagrees.findings] == [
        ("36.00", "35.30")
    ]


def test_listed_prices_both_sides_carry_alike_agree() -> None:
    assert one_line(["35.30", "40.00"], ["40.00", "35.30"]).findings == ()


# Pairing


def test_lines_pair_by_description_regardless_of_order_and_values() -> None:
    result = matched(
        record(
            line(description="Hex key set", quantity="3", amount_gross="45.00"),
            line(description="Work gloves", quantity="5", amount_gross="50.00"),
        ),
        record(
            line(description="Work gloves", quantity="5", amount_gross="40.00"),
            line(description="Hex key set", quantity="3", amount_gross="45.00"),
        ),
    )

    assert result.pairings == (
        Pairing(invoice_line=0, po_line=1, code=None, description=1.0),
        Pairing(invoice_line=1, po_line=0, code=None, description=1.0),
    )
    assert [each.place for each in result.findings] == [Place("po line", 0)]


def test_identity_outranks_any_amount_of_value_agreement() -> None:
    """The line whose description agrees is the pair, though every value of the
    other line agrees."""
    result = matched(
        record(line(description="Hex key set", quantity="3", amount_gross="45.00")),
        record(
            line(description="Work gloves", quantity="3", amount_gross="45.00"),
            line(description="Hex key set", quantity="4", amount_gross="60.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(0, 1)]


def test_values_break_a_tie_between_lines_sharing_a_description() -> None:
    result = matched(
        record(
            line(description="Blue widget", quantity="2", amount_gross="100.00"),
            line(description="Blue widget", quantity="5", amount_gross="250.00"),
        ),
        record(
            line(description="Blue widget", quantity="5", amount_gross="250.00"),
            line(description="Blue widget", quantity="2", amount_gross="100.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]


def test_lines_carrying_neither_code_nor_description_pair_by_values() -> None:
    result = matched(
        record(line(quantity="2", amount_gross="100.00"), line(quantity="5")),
        record(line(quantity="5"), line(quantity="2", amount_gross="100.00")),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]


def test_a_code_scores_beside_the_description_and_is_none_when_a_side_lacks_one() -> (
    None
):
    with_codes = matched(
        record(line(code="HK-100", description="Hex key set")),
        record(line(code="HK-100", description="Hex key set")),
    )
    without = matched(
        record(line(code="HK-100", description="Hex key set")),
        record(line(description="Hex key set")),
    )

    assert with_codes.pairings[0].code == 1.0
    assert without.pairings[0].code is None


def test_a_code_agreement_pairs_lines_whose_descriptions_differ() -> None:
    result = matched(
        record(line(code="HK-100", description="Hex key set, metric")),
        record(
            line(code="WG-200", description="Hex key set, metric"),
            line(code="HK-100", description="Allen keys"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(0, 0)]


def test_a_pair_below_the_floor_is_left_unpaired_with_its_best_candidate() -> None:
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(line(description="Work gloves", quantity="1")),
    )

    assert result.pairings == ()
    (unpaired,) = result.unpaired_invoice
    assert unpaired.line == 0
    assert unpaired.candidate is not None
    assert unpaired.candidate.line == 0
    assert unpaired.candidate.code is None
    assert unpaired.candidate.description == pytest.approx(1 - 10 / 13)
    assert unpaired.candidate.reason == "below the floor"
    assert [each.line for each in result.unpaired_po] == [0]


def test_a_candidate_taken_by_another_line_is_the_reason_a_line_is_unpaired() -> None:
    result = matched(
        record(
            line(description="Blue widget", quantity="2"),
            line(description="Blue widget", quantity="5"),
        ),
        record(line(description="Blue widget", quantity="5")),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(1, 0)]
    assert result.unpaired_invoice == (
        Unpaired(
            line=0,
            candidate=Candidate(
                line=0, code=None, description=1.0, reason="taken by another line"
            ),
        ),
    )


def test_values_do_not_pair_lines_when_only_one_side_names_the_item() -> None:
    """A description on one side and a code on the other say nothing about
    being the same item, and values alone do not either."""
    result = matched(
        record(line(description="Blue widget", quantity="2", amount_gross="100.00")),
        record(line(code="BW-1", quantity="2", amount_gross="100.00")),
    )

    assert result.pairings == ()
    (unpaired,) = result.unpaired_invoice
    assert unpaired.candidate is not None
    assert unpaired.candidate.reason == "no agreement"


def test_a_line_with_nothing_to_compare_against_has_no_candidate() -> None:
    result = matched(record(line(description="Torque wrench")), record())

    assert result.unpaired_invoice == (Unpaired(line=0, candidate=None),)


# Quantities


def shipped(
    invoice_quantity: str | Sequence[str],
    po_quantity: str | Sequence[str],
    received: str | Sequence[str],
) -> MatchResult:
    """One line billed, ordered and received in the quantities given."""
    return match(
        record(line(description="Work gloves", quantity=invoice_quantity)),
        record(line(description="Work gloves", quantity=po_quantity)),
        ReceivingRecord(
            (ReceiptLine(line(description="Work gloves", quantity=received), 0),)
        ),
    )


def test_billing_one_unit_above_the_receipt_is_a_short_ship() -> None:
    result = shipped("12", "12", "11")

    assert result.findings == (
        Finding(
            type="short-ship",
            place=Place("po line", 0),
            cell="quantity",
            invoice=("12",),
            purchase_order=(),
            receipt=("11",),
            margin=Decimal(0),
            tolerance=QUANTITY,
        ),
    )
    assert result.verdict == "held"


def test_ordering_below_both_the_invoice_and_the_receipt_is_an_over_ship() -> None:
    result = shipped("12", "10", "12")

    assert result.findings == (
        Finding(
            type="over-ship",
            place=Place("po line", 0),
            cell="quantity",
            invoice=("12",),
            purchase_order=("10",),
            receipt=("12",),
            margin=Decimal(0),
            tolerance=QUANTITY,
        ),
    )


@pytest.mark.parametrize(
    ("invoice_quantity", "po_quantity", "received"),
    [
        ("12", "12", "12"),
        ("10", "12", "12"),  # billed below the receipt and the order
        ("10", "10", "12"),  # received more than billed or ordered
        ("12.0", "12", "12,00"),  # the same number, spelled apart
    ],
)
def test_quantities_that_do_not_bill_above_the_buyer_are_not_a_finding(
    invoice_quantity: str, po_quantity: str, received: str
) -> None:
    assert shipped(invoice_quantity, po_quantity, received).findings == ()


def test_quantities_are_compared_exactly() -> None:
    """No percent and no unit of slack: a tenth over is over."""
    assert [each.type for each in shipped("12.1", "12", "12").findings] == [
        "short-ship"
    ]
    assert [each.type for each in shipped("12.1", "12", "12.1").findings] == [
        "over-ship"
    ]


def test_billing_above_the_receipt_alone_is_a_short_ship_and_not_an_over_ship() -> (
    None
):
    assert [each.type for each in shipped("12", "10", "10").findings] == [
        "short-ship"
    ]


def test_listed_quantities_fire_on_the_combination_least_favourable_to_the_buyer() -> (
    None
):
    """The highest invoice value against the lowest received, and both highest
    against the lowest ordered."""
    assert [each.type for each in shipped(["10", "12"], "12", "11").findings] == [
        "short-ship"
    ]
    assert [each.type for each in shipped("12", ["12", "10"], "12").findings] == [
        "over-ship"
    ]


def test_sides_listing_the_same_quantities_agree() -> None:
    """An invoice listing ordered and shipped columns, copied as labeled into
    the purchase order and the receipt, is clean: nothing is hidden when both
    sides disagree with themselves the same way."""
    assert shipped(["10", "8"], ["8", "10.0"], ["10", "8"]).findings == ()


def test_listed_quantities_on_one_side_fire_against_a_single_one_below_them() -> (
    None
):
    assert [each.type for each in shipped(["10", "8"], ["10", "8"], "9").findings] == [
        "short-ship"
    ]


def test_a_finding_on_quantities_explains_itself_with_the_values() -> None:
    short, over = shipped("12", "10", "11").findings

    assert explain(short) == (
        'short-ship, PO line 0, quantity, invoice "12" vs receipt "11", '
        "compared exactly"
    )
    assert explain(over) == (
        'over-ship, PO line 0, quantity, invoice "12" and receipt "11" vs PO "10", '
        "compared exactly"
    )


def test_a_receipt_line_follows_the_po_line_it_names_not_its_own_order() -> None:
    result = match(
        record(
            line(description="Hex key set", quantity="3"),
            line(description="Work gloves", quantity="5"),
        ),
        record(
            line(description="Work gloves", quantity="5"),
            line(description="Hex key set", quantity="3"),
        ),
        ReceivingRecord(
            (
                ReceiptLine(line(description="Hex key set", quantity="3"), po_line=1),
                ReceiptLine(line(description="Work gloves", quantity="4"), po_line=0),
            )
        ),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("short-ship", Place("po line", 0))
    ]


def test_a_quantity_the_receipt_lacks_is_not_compared() -> None:
    result = shipped("12", "10", ())

    assert result.findings == ()
    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("12",), "absent"),
        ("quantity", ("10",), "absent"),
    ]


def test_a_short_ship_needs_no_po_quantity_and_the_over_ship_is_not_compared() -> (
    None
):
    result = shipped("12", (), "10")

    assert [each.type for each in result.findings] == ["short-ship"]
    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("12",), "absent")
    ]


def test_a_po_quantity_left_without_its_partners_is_listed_absent() -> None:
    """The over-ship needed it, and neither the invoice nor the receipt carries
    one to compare it with."""
    result = shipped((), "5", ())

    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("5",), "absent")
    ]


def test_every_quantity_an_invoice_without_one_leaves_is_listed_absent() -> None:
    result = shipped((), "5", "5")

    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("5",), "absent"),
        ("quantity", ("5",), "absent"),
    ]


def test_an_unreadable_po_quantity_leaves_the_short_ship_compared() -> None:
    result = shipped("12", "ten", "10")

    assert [each.type for each in result.findings] == ["short-ship"]
    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("ten",), "unreadable")
    ]


def test_an_unreadable_quantity_is_not_compared() -> None:
    result = shipped("twelve", "10", "12")

    assert result.findings == ()
    assert [(each.cell, each.text, each.reason) for each in result.not_compared] == [
        ("quantity", ("twelve",), "unreadable")
    ]


def test_a_po_line_nothing_was_received_against_compares_no_quantity() -> None:
    """The receiving record does not cover the line: there is no receipt
    quantity to be short of, and the over-ship needs one too."""
    result = matched(
        record(line(description="Work gloves", quantity="12")),
        record(line(description="Work gloves", quantity="10")),
    )

    assert result.findings == ()
    assert result.not_compared == ()


def test_a_receiving_record_names_each_po_line_at_most_once() -> None:
    with pytest.raises(ValueError, match="po line 0"):
        ReceivingRecord(
            (
                ReceiptLine(line(quantity="2"), po_line=0),
                ReceiptLine(line(quantity="3"), po_line=0),
            )
        )


# The verdict


def test_held_when_any_finding_is_a_hold() -> None:
    assert one_line("35.66", "35.30").verdict == "held"


def test_approvable_when_there_is_no_finding() -> None:
    assert one_line("35.30", "35.30").verdict == "approvable"
