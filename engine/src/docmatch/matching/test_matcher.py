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
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.matching.tolerances import PRICE

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
    assert result.unpaired_invoice == (Unpaired(line=0, candidate=extra.candidate),)
    assert result.unpaired_po == (Unpaired(line=0, candidate=missing.candidate),)


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
