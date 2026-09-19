"""Tests for the generator on a small seed pool: seam 2 of the Phase 2 spec.

Every seed here is made up. They pin what a case's records and truth look
like, never how the draw is made.
"""

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal

import pytest

from docmatch.matching.generator import Case, GeneratorError, Seed, generate
from docmatch.matching.matcher import DiscrepancyType, Place
from docmatch.matching.records import Cell, Record, cell_values
from docmatch.matching.tolerances import PRICE, band
from docmatch.metrics.normalization import read_number

FieldValuesDict = dict[str, tuple[str, ...]]


def line(**cells: str | Sequence[str]) -> FieldValuesDict:
    return {
        f"line_item_{name}": (value,) if isinstance(value, str) else tuple(value)
        for name, value in cells.items()
    }


def seed(document_id: str, *lines: FieldValuesDict, **header: str) -> Seed:
    return Seed(
        document_id,
        Record(
            header={fieldtype: (value,) for fieldtype, value in header.items()},
            lines=lines,
        ),
    )


PRICED = seed(
    "priced",
    line(description="Hex key set", quantity="3", unit_price_gross="15.00"),
    line(description="Torque wrench", quantity="1", amount_gross="317.50"),
    amount_total_tax="18.00",
)
AMOUNTS = seed(
    "amounts",
    line(description="Cable reel, 25 m", quantity="10", amount_net="900,00"),
    line(description="Junction box", quantity="4", amount_net="420,00"),
)
NO_MONEY = seed("no-money", line(description="Lunch platter", quantity="2"))
NO_LINES = seed("no-lines", vendor_name="Pinefield Catering")
POOL = (PRICED, AMOUNTS, NO_MONEY, NO_LINES)


def injected(
    cases: Sequence[Case], type_: DiscrepancyType = "price variance"
) -> list[Case]:
    return [case for case in cases if case.truth and case.truth[0].type == type_]


def test_the_invoice_is_the_seed_in_every_case() -> None:
    cases = generate(POOL, seed=1, clean=4, per_type=4)

    assert len(cases) == 4 + 4 * 3  # clean, then each injected type
    by_id = {each.document_id: each.invoice for each in POOL}
    assert all(case.invoice == by_id[case.document_id] for case in cases)


def test_a_clean_case_copies_every_line_as_labeled_into_the_po_and_receipt() -> None:
    cases = generate(POOL, seed=1, clean=3, per_type=0)

    assert [case.truth for case in cases] == [(), (), ()]
    for case in cases:
        assert case.purchase_order == case.invoice
        assert [each.po_line for each in case.receipt.lines] == list(
            range(len(case.invoice.lines))
        )


def test_the_receiving_record_carries_quantities_and_never_prices() -> None:
    (case,) = generate((PRICED,), seed=1, clean=1, per_type=0)

    assert [dict(each.cells) for each in case.receipt.lines] == [
        line(description="Hex key set", quantity="3"),
        line(description="Torque wrench", quantity="1"),
    ]


def test_a_price_variance_lowers_the_po_unit_price_where_the_seed_has_one() -> None:
    cases = injected(generate((PRICED,), seed=1, clean=0, per_type=6))

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        assert truth.type == "price variance"
        assert truth.place.kind == "po line"
        assert truth.place.line is not None
        po_line = case.purchase_order.lines[truth.place.line]
        seed_line = PRICED.invoice.lines[truth.place.line]
        cell: Cell = "unit price" if truth.place.line == 0 else "amount"
        lowered, seeded = cell_values(po_line, cell), cell_values(seed_line, cell)
        assert lowered is not None and seeded is not None
        lowered_value = read_number(lowered.texts[0])
        seeded_value = read_number(seeded.texts[0])
        assert lowered_value is not None and seeded_value is not None
        assert lowered_value < seeded_value
        untouched = [
            each
            for position, each in enumerate(case.purchase_order.lines)
            if position != truth.place.line
        ]
        assert untouched == [
            each
            for position, each in enumerate(PRICED.invoice.lines)
            if position != truth.place.line
        ]


def test_injections_draw_near_and_far_half_and_half_within_their_bands() -> None:
    cases = injected(generate((PRICED, AMOUNTS), seed=1, clean=0, per_type=10))

    bands = Counter(truth.band for case in cases for truth in case.truth)
    assert bands == {"near": 5, "far": 5}
    for case in cases:
        (truth,) = case.truth
        assert truth.place.line is not None
        seed_line = case.invoice.lines[truth.place.line]
        po_line = case.purchase_order.lines[truth.place.line]
        cell: Cell = "unit price" if cell_values(seed_line, "unit price") else "amount"
        invoice_values = cell_values(seed_line, cell)
        po_values = cell_values(po_line, cell)
        assert invoice_values is not None and po_values is not None
        invoice_value = read_number(invoice_values.texts[0])
        po_value = read_number(po_values.texts[0])
        assert invoice_value is not None and po_value is not None
        assert band(PRICE, invoice_value, po_value) == truth.band


def test_a_lowered_value_keeps_the_seed_value_decimal_places() -> None:
    cases = injected(generate((AMOUNTS,), seed=1, clean=0, per_type=2))

    for case in cases:
        (truth,) = case.truth
        assert truth.place.line is not None
        lowered = cell_values(case.purchase_order.lines[truth.place.line], "amount")
        assert lowered is not None
        value = read_number(lowered.texts[0])
        assert value is not None
        assert value == value.quantize(Decimal("0.01"))


def test_a_rare_type_reuses_seeds_with_fresh_draws() -> None:
    cases = injected(generate((PRICED,), seed=1, clean=0, per_type=6))

    touched: tuple[tuple[int, Cell], ...] = ((0, "unit price"), (1, "amount"))
    lowered = {
        cell_values(case.purchase_order.lines[position], cell)
        for case in cases
        for position, cell in touched
    }
    assert len(cases) == 6
    assert len(lowered) > 2  # fresh draws, not one lowered value repeated


def test_the_same_seed_rebuilds_the_same_cases_and_another_seed_others() -> None:
    first = generate(POOL, seed=1, clean=4, per_type=4)

    assert generate(POOL, seed=1, clean=4, per_type=4) == first
    assert generate(POOL, seed=2, clean=4, per_type=4) != first


def test_a_document_with_no_lines_seeds_nothing() -> None:
    cases = generate((NO_LINES, PRICED), seed=1, clean=4, per_type=2)

    assert {case.document_id for case in cases} == {"priced"}


def test_a_type_is_injected_only_on_lines_carrying_its_cells() -> None:
    cases = injected(generate((NO_MONEY, AMOUNTS), seed=1, clean=0, per_type=4))

    assert {case.document_id for case in cases} == {"amounts"}
    assert all(
        truth.place == Place("po line", 0) or truth.place == Place("po line", 1)
        for case in cases
        for truth in case.truth
    )


def test_a_pool_with_no_eligible_seed_injects_nothing() -> None:
    """The type then reads with n 0 in the report rather than failing it."""
    cases = generate((NO_MONEY, NO_LINES), seed=1, clean=2, per_type=1)

    assert injected(cases) == []


def test_a_pool_whose_values_cannot_reach_a_band_is_an_error() -> None:
    """A cent-sized value has no near band: the cent floor swallows it."""
    tiny = seed("tiny", line(description="Washer", amount_gross="0.03"))

    with pytest.raises(GeneratorError, match="near price variance"):
        generate((tiny,), seed=1, clean=0, per_type=1)


# Extra line and missing line


def test_an_extra_line_removes_a_line_from_the_po_and_the_receipt() -> None:
    cases = injected(
        generate((PRICED, AMOUNTS), seed=1, clean=0, per_type=6), "extra line"
    )

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        assert truth.type == "extra line"
        assert truth.place.kind == "invoice line"
        assert truth.band is None
        removed = truth.place.line
        assert removed is not None
        kept = [
            each
            for position, each in enumerate(case.invoice.lines)
            if position != removed
        ]
        assert list(case.purchase_order.lines) == kept
        assert [each.po_line for each in case.receipt.lines] == list(range(len(kept)))


def test_a_missing_line_adds_a_labeled_line_from_another_seed_to_the_po() -> None:
    cases = injected(generate(POOL, seed=1, clean=0, per_type=6), "missing line")

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        assert truth.type == "missing line"
        assert truth.place.kind == "po line"
        assert truth.band is None
        added = truth.place.line
        assert added is not None
        po_lines = list(case.purchase_order.lines)
        assert po_lines[:added] + po_lines[added + 1 :] == list(case.invoice.lines)
        donors = [
            each.invoice.lines for each in POOL if each.document_id != case.document_id
        ]
        assert any(po_lines[added] in lines for lines in donors)
        assert [each.po_line for each in case.receipt.lines] == list(
            range(len(po_lines))
        )


def test_a_missing_line_needs_another_seed_in_the_pool() -> None:
    """A pool of one seed has no other line to add: n 0, not an error."""
    cases = generate((PRICED,), seed=1, clean=0, per_type=2)

    assert {case.truth[0].type for case in cases} == {"price variance", "extra line"}


def test_a_line_the_records_could_not_tell_apart_is_never_the_one_injected() -> None:
    """Two lines alike on every cell pairing reads leave no way to say which
    one was removed or added, so the truth could not be scored: such lines
    seed neither, whatever else they carry."""
    doubled = seed(
        "doubled",
        line(description="Freight", amount_gross="12.00", date="2026-03-01"),
        line(description="Freight", amount_gross="12,00", date="2026-03-08"),
        line(description="Crate", amount_gross="40.00"),
    )
    donor = seed("donor", line(description="freight", amount_gross="12"))

    cases = generate((doubled, donor), seed=1, clean=0, per_type=6)

    extra = injected(cases, "extra line")
    missing = injected(cases, "missing line")
    assert {case.truth[0].place for case in extra if case.document_id == "doubled"} == {
        Place("invoice line", 2)
    }
    assert {case.document_id for case in missing} == {"donor"}


def test_a_line_with_nothing_to_pair_on_is_never_removed_or_added() -> None:
    """The matcher does not compare such a line, so injecting it could not
    be found."""
    dated = seed(
        "dated", line(date="2026-03-01"), line(description="Crate", amount_gross="40")
    )
    positioned = seed(
        "positioned", line(position="1"), line(description="Pallet", amount_gross="9")
    )

    cases = generate((dated, positioned), seed=1, clean=0, per_type=6)

    assert {case.truth[0].place for case in injected(cases, "extra line")} == {
        Place("invoice line", 1)
    }
    for case in injected(cases, "missing line"):
        added = case.truth[0].place.line
        assert added is not None
        assert "line_item_amount_gross" in case.purchase_order.lines[added]
