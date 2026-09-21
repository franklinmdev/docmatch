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
    NotCompared,
    Pairing,
    Place,
    Unpaired,
    UnpairedFinding,
    explain,
    match,
)
from docmatch.matching.records import ReceiptLine, ReceivingRecord, Record
from docmatch.matching.tolerances import PRICE, QUANTITY, TAX, UNIT

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


def compared(result: MatchResult) -> list[Finding]:
    """The findings that compared a value, leaving out unpaired lines."""
    return [each for each in result.findings if isinstance(each, Finding)]


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
    assert [each.cell for each in compared(one_line("0.52", "0.50"))] == ["unit price"]


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
        (each.cell, each.invoice, each.purchase_order) for each in compared(result)
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

    assert [each.cell for each in compared(result)] == ["amount"]
    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
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
    assert [each.cell for each in compared(result)] == ["amount"]


def test_a_net_column_is_compared_when_there_is_no_gross_one() -> None:
    result = matched(
        record(line(description="Hex key set", unit_price_net="46.00")),
        record(line(description="Hex key set", unit_price_gross="45.00")),
    )

    assert [each.cell for each in compared(result)] == ["unit price"]


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
    assert [each.invoice for each in compared(invoice_disagrees)] == [
        ("35.30", "35.66")
    ]

    po_disagrees = one_line("35.66", ["36.00", "35.30"])
    assert [each.purchase_order for each in compared(po_disagrees)] == [
        ("36.00", "35.30")
    ]


def test_listed_prices_both_sides_carry_alike_agree() -> None:
    assert one_line(["35.30", "40.00"], ["40.00", "35.30"]).findings == ()


# Tax mismatch


def taxed(invoice_tax: str | None, po_tax: str | None) -> MatchResult:
    """One line alike on both sides, so only the header can differ."""
    item = line(description="Torque wrench", unit_price_gross="35.30")
    return matched(
        record(
            item, **({} if invoice_tax is None else {"amount_total_tax": invoice_tax})
        ),
        record(item, **({} if po_tax is None else {"amount_total_tax": po_tax})),
    )


@pytest.mark.parametrize("invoice_tax", ["35.31", "35.65", "35.30", "20.00"])
def test_a_header_tax_within_the_tolerance_or_below_is_not_a_finding(
    invoice_tax: str,
) -> None:
    assert taxed(invoice_tax, "35.30").findings == ()


def test_a_header_tax_past_the_tolerance_is_a_tax_mismatch_on_the_header() -> None:
    result = taxed("35.66", "35.30")

    assert result.findings == (
        Finding(
            type="tax mismatch",
            place=Place("header"),
            cell="tax",
            invoice=("35.66",),
            purchase_order=("35.30",),
            margin=Decimal("0.353"),
            tolerance=TAX,
        ),
    )
    assert result.verdict == "held"


def test_a_tax_mismatch_explains_itself_on_the_header() -> None:
    (finding,) = taxed("35.66", "35.30").findings

    assert explain(finding) == (
        'tax mismatch, header, tax, invoice "35.66" vs PO "35.30", '
        "margin 0.353 (1 percent of 35.30, above 0.01)"
    )


def test_the_cent_floor_holds_on_a_small_tax() -> None:
    assert taxed("0.51", "0.50").findings == ()
    assert [each.type for each in taxed("0.52", "0.50").findings] == ["tax mismatch"]


def test_a_tax_one_side_lacks_is_listed_on_the_header_as_absent() -> None:
    result = taxed("35.66", None)

    assert result.findings == ()
    assert [
        (each.place, each.cell, each.texts, each.reason) for each in result.not_compared
    ] == [(Place("header"), "tax", ("35.66",), "absent")]


def test_an_unreadable_tax_is_listed_and_not_compared() -> None:
    result = taxed("n/a", "35.30")

    assert result.findings == ()
    assert [(each.place, each.reason) for each in result.not_compared] == [
        (Place("header"), "unreadable")
    ]


def test_no_tax_on_either_side_lists_nothing() -> None:
    result = taxed(None, None)

    assert result.findings == ()
    assert result.not_compared == ()


def test_listed_header_taxes_fire_on_the_least_favourable_combination() -> None:
    """As on a line: the highest invoice tax against the lowest PO tax when
    the two sides list different taxes; sides listing the same taxes agree."""
    item = line(description="Torque wrench", unit_price_gross="35.30")
    invoice = Record({"amount_total_tax": ("5.00", "15.00")}, (item,))
    purchase_order = Record({"amount_total_tax": ("5.00", "14.00")}, (item,))

    result = matched(invoice, purchase_order)

    assert [
        (each.place, each.invoice, each.purchase_order) for each in compared(result)
    ] == [(Place("header"), ("5.00", "15.00"), ("5.00", "14.00"))]
    assert matched(invoice, invoice).findings == ()


def test_a_tax_mismatch_is_found_beside_a_price_variance() -> None:
    result = matched(
        record(
            line(description="Torque wrench", unit_price_gross="35.66"),
            amount_total_tax="35.66",
        ),
        record(
            line(description="Torque wrench", unit_price_gross="35.30"),
            amount_total_tax="35.30",
        ),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("price variance", Place("po line", 0)),
        ("tax mismatch", Place("header")),
    ]


# Unit variant


def counted(
    invoice_unit: str | Sequence[str], po_unit: str | Sequence[str]
) -> MatchResult:
    """One line alike on both sides but for its unit."""
    return matched(
        record(line(description="Copier paper", units_of_measure=invoice_unit)),
        record(line(description="Copier paper", units_of_measure=po_unit)),
    )


def test_a_unit_differing_from_the_po_lines_is_a_unit_variant_on_the_po_line() -> None:
    result = counted("BOX", "EA")

    assert result.findings == (
        Finding(
            type="unit variant",
            place=Place("po line", 0),
            cell="unit",
            invoice=("BOX",),
            purchase_order=("EA",),
            margin=Decimal(0),
            tolerance=UNIT,
        ),
    )
    assert result.verdict == "held"


def test_a_unit_variant_suppresses_price_and_quantity_on_its_line() -> None:
    """Every value here would fire on its own; counted in another unit, none
    is comparable without a conversion table (#66)."""
    result = match(
        record(
            line(
                description="Copier paper",
                units_of_measure="BOX",
                quantity="24",
                unit_price_gross="30.00",
                amount_gross="720.00",
            )
        ),
        record(
            line(
                description="Copier paper",
                units_of_measure="EA",
                quantity="2",
                unit_price_gross="2.50",
                amount_gross="5.00",
            )
        ),
        ReceivingRecord(
            (ReceiptLine(line(units_of_measure="EA", quantity="2"), po_line=0),)
        ),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("unit variant", Place("po line", 0))
    ]
    assert result.not_compared == (
        NotCompared(Place("po line", 0), "unit price", ("30.00",), "unit variant"),
        NotCompared(Place("po line", 0), "amount", ("720.00",), "unit variant"),
        NotCompared(Place("po line", 0), "quantity", ("24",), "unit variant"),
    )


def test_a_unit_one_side_lacks_is_listed_absent_and_the_line_compared_as_usual() -> (
    None
):
    result = matched(
        record(
            line(
                description="Copier paper",
                units_of_measure="BOX",
                unit_price_gross="35.66",
            )
        ),
        record(line(description="Copier paper", unit_price_gross="35.30")),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("price variance", Place("po line", 0))
    ]
    assert result.not_compared == (
        NotCompared(Place("po line", 0), "unit", ("BOX",), "absent"),
    )


@pytest.mark.parametrize(
    ("invoice_unit", "po_unit"),
    [
        ("EA", " ea "),
        (["BOX", "EA"], ["ea", "Box"]),  # sides listing the same units agree
    ],
)
def test_units_alike_after_text_normalization_are_not_a_finding(
    invoice_unit: str | Sequence[str], po_unit: str | Sequence[str]
) -> None:
    result = counted(invoice_unit, po_unit)

    assert result.findings == ()
    assert result.not_compared == ()


def test_a_unit_listed_on_one_side_only_is_a_unit_variant() -> None:
    """As with listed values: a reading that disagrees with itself cannot
    hide a unit the purchase order was not counted in."""
    assert [each.type for each in counted(["BOX", "EA"], "EA").findings] == [
        "unit variant"
    ]


def test_a_unit_variant_explains_itself_with_the_units() -> None:
    (finding,) = counted("BOX", "EA").findings

    assert explain(finding) == (
        'unit variant, PO line 0, unit, invoice "BOX" vs PO "EA", compared exactly'
    )


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


def test_a_one_cent_drift_on_the_separating_cell_still_pairs_the_lines() -> None:
    """The two lines share a description and a quantity, so their amount is
    the only cell telling them apart, and a cent moves it on both
    purchase-order lines. Exact agreement leaves a perfect tie, which the
    assignment breaks by position; closeness still puts each line with the
    amount it is nearest (#102)."""
    result = matched(
        record(
            line(description="Blue widget", quantity="2", amount_gross="100.00"),
            line(description="Blue widget", quantity="2", amount_gross="250.00"),
        ),
        record(
            line(description="Blue widget", quantity="2", amount_gross="250.01"),
            line(description="Blue widget", quantity="2", amount_gross="99.99"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]


def test_a_closer_identity_outranks_perfect_value_closeness_on_every_line() -> None:
    """Both assignments are above the floor, and the one values prefer agrees
    on every value of both lines: identity still decides, so the scale is
    above everything closeness can sum to (#102)."""
    result = matched(
        record(
            line(description="Blue widget", quantity="1", amount_gross="1.00"),
            line(description="Blue widgets", quantity="100", amount_gross="1000.00"),
        ),
        record(
            line(description="Blue widgets", quantity="1", amount_gross="1.00"),
            line(description="Blue widget", quantity="100", amount_gross="1000.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]


def test_two_zero_values_are_as_close_as_two_that_agree() -> None:
    result = matched(
        record(line(description="Blue widget", amount_gross="0.00")),
        record(
            line(description="Blue widget", amount_gross="50.00"),
            line(description="Blue widget", amount_gross="0.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(0, 1)]


def test_values_of_different_signs_are_no_closer_than_none_at_all() -> None:
    """A credit and a charge of the same size are opposites, not the same
    value; two credits are compared in absolute value (#102)."""
    result = matched(
        record(line(description="Freight credit", amount_gross="-100.00")),
        record(
            line(description="Freight credit", amount_gross="100.00"),
            line(description="Freight credit", amount_gross="-105.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(0, 1)]


def test_the_best_candidate_is_the_closest_by_value_too() -> None:
    """Nothing pairs, since neither line names its item and no value agrees
    exactly; the candidate reported is still the nearest one (#102)."""
    result = matched(
        record(line(quantity="10")),
        record(line(quantity="1"), line(quantity="9")),
    )

    assert result.pairings == ()
    (unpaired,) = result.unpaired_invoice
    assert unpaired.candidate is not None
    assert unpaired.candidate.line == 1
    assert unpaired.candidate.reason == "no agreement"


def test_lines_carrying_neither_code_nor_description_pair_by_values() -> None:
    result = matched(
        record(line(quantity="2", amount_gross="100.00"), line(quantity="5")),
        record(line(quantity="5"), line(quantity="2", amount_gross="100.00")),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]


def test_two_nameless_pairs_that_agree_outrank_one_that_is_merely_close() -> None:
    """Invoice line 0 comes very close to PO line 1 on a cent of drift, but
    taking that one pair alone would leave line 1 and PO line 0 unpaired, a
    false extra line and a false missing line. Among nameless lines exact
    agreement decides how strongly they pair, and closeness only orders the
    pairs that agree on as much (#102)."""
    result = matched(
        record(
            line(quantity="3", unit_price_gross="100.00", amount_gross="200.00"),
            line(amount_gross="55.00"),
        ),
        record(
            line(quantity="3"),
            line(quantity="3", unit_price_gross="100.01", amount_gross="55.00"),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 0),
        (1, 1),
    ]


def test_the_unit_breaks_a_tie_between_nameless_lines_alike_on_every_value() -> None:
    """The two invoice lines agree on quantity, unit price and amount and
    differ only in their unit, and a cent of drift on one purchase-order copy
    leaves the values with nothing to choose by. The unit is one more
    tiebreak, worth one whole agreement of closeness, so each line pairs with
    its own copy and no unit variant fires (#118, #124)."""
    result = matched(
        record(
            line(
                quantity="10",
                unit_price_gross="1.00",
                amount_gross="10.00",
                units_of_measure="hrs",
            ),
            line(
                quantity="10",
                unit_price_gross="1.00",
                amount_gross="10.00",
                units_of_measure="ea",
            ),
        ),
        record(
            line(
                quantity="10",
                unit_price_gross="1.00",
                amount_gross="10.00",
                units_of_measure="EA",
            ),
            line(
                quantity="10",
                unit_price_gross="1.01",
                amount_gross="10.00",
                units_of_measure="HRS",
            ),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
        (1, 0),
    ]
    assert result.findings == ()


def test_a_line_listing_two_units_is_no_closer_to_one_listing_one_of_them() -> None:
    """The tiebreak reads whole listings against each other, as the unit
    variant rule does, so the two never disagree: the copy listing one of the
    line's two units is no closer than the copy listing neither, and the
    amount decides (#124)."""
    result = matched(
        record(
            line(
                description="Copier paper",
                quantity="3",
                amount_gross="30.00",
                units_of_measure=("box", "ea"),
            ),
        ),
        record(
            line(
                description="Copier paper",
                quantity="3",
                amount_gross="20.00",
                units_of_measure="ea",
            ),
            line(
                description="Copier paper",
                quantity="3",
                amount_gross="30.00",
                units_of_measure="pk",
            ),
        ),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [
        (0, 1),
    ]


def test_two_nameless_lines_sharing_only_a_unit_do_not_pair() -> None:
    """Sharing `ea` is no evidence of the same item: the unit is never an
    exact agreement that lets two nameless lines pair, only a tiebreak among
    lines that pair on something else (#118, #124)."""
    result = matched(
        record(line(quantity="2", units_of_measure="ea")),
        record(line(quantity="5", units_of_measure="ea")),
    )

    assert result.pairings == ()
    assert result.findings == (
        UnpairedFinding(
            "extra line",
            Place("invoice line", 0),
            Candidate(0, None, None, "no agreement"),
        ),
        UnpairedFinding(
            "missing line",
            Place("po line", 0),
            Candidate(0, None, None, "no agreement"),
        ),
    )


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
    assert [each.place for each in result.findings if each.type == "missing line"] == [
        Place("po line", 0)
    ]


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


def test_billing_above_the_receipt_alone_is_a_short_ship_and_not_an_over_ship() -> None:
    assert [each.type for each in shipped("12", "10", "10").findings] == ["short-ship"]


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


def test_listed_quantities_on_one_side_fire_against_a_single_one_below_them() -> None:
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
    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
        ("quantity", ("12",), "absent"),
        ("quantity", ("10",), "absent"),
    ]


def test_a_short_ship_needs_no_po_quantity_and_the_over_ship_is_not_compared() -> None:
    result = shipped("12", (), "10")

    assert [each.type for each in result.findings] == ["short-ship"]
    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
        ("quantity", ("12",), "absent"),
        ("quantity", ("10",), "absent"),
    ]


def test_a_po_quantity_left_without_its_partners_is_listed_absent() -> None:
    """The over-ship needed it, and neither the invoice nor the receipt carries
    one to compare it with."""
    result = shipped((), "5", ())

    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
        ("quantity", ("5",), "absent")
    ]


def test_every_quantity_an_invoice_without_one_leaves_is_listed_absent() -> None:
    result = shipped((), "5", "5")

    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
        ("quantity", ("5",), "absent"),
        ("quantity", ("5",), "absent"),
    ]


def test_an_unreadable_po_quantity_leaves_the_short_ship_compared() -> None:
    result = shipped("12", "ten", "10")

    assert [each.type for each in result.findings] == ["short-ship"]
    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
        ("quantity", ("ten",), "unreadable")
    ]


def test_an_unreadable_quantity_is_not_compared() -> None:
    result = shipped("twelve", "10", "12")

    assert result.findings == ()
    assert [(each.cell, each.texts, each.reason) for each in result.not_compared] == [
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


# Unpaired lines as findings


def test_an_unpaired_invoice_line_is_an_extra_line_held_on_the_invoice_line() -> None:
    result = matched(
        record(
            line(description="Torque wrench", quantity="1"),
            line(description="Work gloves", quantity="4"),
        ),
        record(line(description="Torque wrench", quantity="1")),
    )

    assert result.findings == (
        UnpairedFinding(
            type="extra line",
            place=Place("invoice line", 1),
            candidate=Candidate(
                line=0,
                code=None,
                description=1 - 10 / 13,
                reason="below the floor",
            ),
        ),
    )
    assert result.findings[0].severity == "hold"
    assert result.verdict == "held"


def test_an_unpaired_po_line_is_a_missing_line_noted_on_the_po_line() -> None:
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(
            line(description="Work gloves", quantity="4"),
            line(description="Torque wrench", quantity="1"),
        ),
    )

    (finding,) = result.findings
    assert (finding.type, finding.place) == ("missing line", Place("po line", 0))
    assert finding.severity == "note"


def test_a_case_whose_only_finding_is_a_missing_line_is_approvable() -> None:
    """Partial invoicing is normal, so a PO line not billed never holds."""
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(
            line(description="Torque wrench", quantity="1"),
            line(description="Work gloves", quantity="4"),
        ),
    )

    assert [each.type for each in result.findings] == ["missing line"]
    assert result.verdict == "approvable"


def test_every_unpaired_line_is_a_finding_and_names_its_candidate() -> None:
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(line(description="Work gloves", quantity="4")),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("extra line", Place("invoice line", 0)),
        ("missing line", Place("po line", 0)),
    ]
    extra, missing = result.findings
    assert isinstance(extra, UnpairedFinding) and isinstance(missing, UnpairedFinding)
    assert extra.candidate is not None and missing.candidate is not None
    assert result.unpaired_invoice == (Unpaired(line=0, candidate=extra.candidate),)


def test_an_extra_line_leaves_a_price_variance_on_its_own_po_line() -> None:
    """The PO dropped the invoice's second line, so its third line is PO line
    1, and pairing holds: the overbilled line is still found where it is."""
    result = matched(
        record(
            line(description="Hex key set", unit_price_gross="15.00"),
            line(description="Work gloves", unit_price_gross="4.00"),
            line(description="Torque wrench", unit_price_gross="35.66"),
        ),
        record(
            line(description="Hex key set", unit_price_gross="15.00"),
            line(description="Torque wrench", unit_price_gross="35.30"),
        ),
    )

    assert [(each.type, each.place) for each in result.findings] == [
        ("price variance", Place("po line", 1)),
        ("extra line", Place("invoice line", 1)),
    ]


def test_an_unpaired_finding_explains_its_closest_candidate() -> None:
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(line(description="Work gloves", quantity="4")),
    )

    assert [explain(each) for each in result.findings] == [
        "extra line, invoice line 0, closest PO line 0, code: no code, "
        "description 0.231, below the floor",
        "missing line, PO line 0, closest invoice line 0, code: no code, "
        "description 0.231, below the floor",
    ]


def test_an_unpaired_line_with_nothing_on_the_other_side_explains_so() -> None:
    (finding,) = matched(record(line(description="Torque wrench")), record()).findings

    assert explain(finding) == "extra line, invoice line 0, the PO has no lines"


def test_a_nameless_line_whose_only_price_disagrees_is_extra_plus_missing() -> None:
    """With no code or description, values are all that pair a line; lower
    its one price on the PO and nothing is left to agree on (#82)."""
    result = matched(
        record(line(amount_gross="40.00")), record(line(amount_gross="30.00"))
    )

    assert result.pairings == ()
    assert [(each.type, each.place) for each in result.findings] == [
        ("extra line", Place("invoice line", 0)),
        ("missing line", Place("po line", 0)),
    ]
    assert explain(result.findings[0]) == (
        "extra line, invoice line 0, closest PO line 0, code: no code, "
        "description: no description, no agreement"
    )


def test_a_line_with_nothing_to_pair_on_is_not_compared_and_not_a_finding() -> None:
    """A row labeled with only a date carries no code, description, quantity,
    price or amount: it is not an item, so it pairs with nothing and holds
    nothing, on either side."""
    dated = line(date="2026-03-01")
    result = matched(
        record(line(description="Torque wrench", quantity="1"), dated),
        record(dated, line(description="Torque wrench", quantity="1")),
    )

    assert [(each.invoice_line, each.po_line) for each in result.pairings] == [(0, 1)]
    assert result.findings == ()
    assert result.not_compared == (
        NotCompared(Place("invoice line", 1), None, (), "nothing to pair on"),
        NotCompared(Place("po line", 0), None, (), "nothing to pair on"),
    )
    assert result.verdict == "approvable"


def test_a_line_with_nothing_to_pair_on_is_never_a_candidate() -> None:
    result = matched(
        record(line(description="Torque wrench", quantity="1")),
        record(line(position="1"), line(description="Work gloves", quantity="4")),
    )

    extra, missing = result.findings
    assert isinstance(extra, UnpairedFinding) and extra.candidate is not None
    assert extra.candidate.line == 1
    assert missing.place == Place("po line", 1)


# The verdict


def test_held_when_any_finding_is_a_hold() -> None:
    assert one_line("35.66", "35.30").verdict == "held"


def test_approvable_when_there_is_no_finding() -> None:
    assert one_line("35.30", "35.30").verdict == "approvable"


def test_what_was_not_compared_never_holds() -> None:
    """A misread number is listed, never an eighth type or a forced hold (#76)."""
    result = taxed("n/a", "35.30")

    assert result.not_compared
    assert result.verdict == "approvable"
