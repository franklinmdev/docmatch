"""Tests for the generator on a small seed pool: seam 2 of the Phase 2 spec.

Every seed here is made up. They pin what a case's records and truth look
like, never how the draw is made.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from docmatch.matching.generator import (
    HARD_NEGATIVE_KINDS,
    INJECTED_TYPES,
    Case,
    GeneratorError,
    HardNegative,
    HardNegativeKind,
    Injected,
    Seed,
    documents_carrying,
    generate,
)
from docmatch.matching.matcher import DiscrepancyType, Place, match
from docmatch.matching.records import (
    CELL_FIELDTYPES,
    Cell,
    ReceivingRecord,
    Record,
    cell_values,
)
from docmatch.matching.tolerances import PRICE, TAX, band, quantity_band
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import normalize_text, read_number

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
    line(
        description="Hex key set",
        quantity="6",
        units_of_measure="SET",
        unit_price_gross="15.00",
    ),
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


def single(
    seeds: Sequence[Seed], *, seed: int, clean: int, per_type: int
) -> list[Case]:
    """Cases carrying one discrepancy each, with every hard negative put back
    as labeled, so a test sees what its injection did and nothing else."""
    return [
        as_labeled(case)
        for case in generate(seeds, seed=seed, clean=clean, per_type=per_type, most=1)
    ]


def as_labeled(case: Case) -> Case:
    po_lines = list(case.purchase_order.lines)
    receipt_lines = list(case.receipt.lines)
    for negative in case.hard_negatives:
        position = negative.place.line
        assert position is not None
        seeded = case.invoice.lines[negative.invoice_line]
        po_lines[position] = seeded
        receipt_lines = [
            replace(
                each,
                cells={
                    **each.cells,
                    **{
                        fieldtype: seeded[fieldtype]
                        for fieldtype in CELL_FIELDTYPES[negative.cell]
                        if fieldtype in each.cells
                    },
                },
            )
            if each.po_line == position
            else each
            for each in receipt_lines
        ]
    return replace(
        case,
        purchase_order=replace(case.purchase_order, lines=tuple(po_lines)),
        receipt=ReceivingRecord(tuple(receipt_lines)),
        hard_negatives=(),
    )


def injected(
    cases: Sequence[Case], type_: DiscrepancyType = "price variance"
) -> list[Case]:
    """The cases carrying an injection of the type."""
    return [case for case in cases if case.truth and case.truth[0].type == type_]


def test_the_invoice_is_the_seed_in_every_case() -> None:
    cases = generate(POOL, seed=1, clean=4, per_type=4)

    assert len(cases) == 4 + 4 * len(INJECTED_TYPES)
    by_id = {each.document_id: each.invoice for each in POOL}
    assert all(case.invoice == by_id[case.document_id] for case in cases)


def test_a_clean_case_copies_every_line_but_its_hard_negatives() -> None:
    cases = single(POOL, seed=1, clean=3, per_type=0)

    assert [case.truth for case in cases] == [(), (), ()]
    for case in cases:
        assert case.purchase_order == case.invoice
        assert [each.po_line for each in case.receipt.lines] == list(
            range(len(case.invoice.lines))
        )


def test_the_receiving_record_carries_quantities_and_never_prices() -> None:
    (case,) = single((PRICED,), seed=1, clean=1, per_type=0)

    assert [dict(each.cells) for each in case.receipt.lines] == [
        line(description="Hex key set", quantity="6", units_of_measure="SET"),
        line(description="Torque wrench", quantity="1"),
    ]


def test_a_price_variance_lowers_the_po_unit_price_where_the_seed_has_one() -> None:
    cases = injected(single((PRICED,), seed=1, clean=0, per_type=6), "price variance")

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
    cases = injected(
        single((PRICED, AMOUNTS), seed=1, clean=0, per_type=10), "price variance"
    )

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
    cases = injected(single((AMOUNTS,), seed=1, clean=0, per_type=2), "price variance")

    for case in cases:
        (truth,) = case.truth
        assert truth.place.line is not None
        lowered = cell_values(case.purchase_order.lines[truth.place.line], "amount")
        assert lowered is not None
        value = read_number(lowered.texts[0])
        assert value is not None
        assert value == value.quantize(Decimal("0.01"))


def test_a_rare_type_reuses_seeds_with_fresh_draws() -> None:
    cases = injected(single((PRICED,), seed=1, clean=0, per_type=6), "price variance")

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
    cases = single((NO_LINES, PRICED), seed=1, clean=4, per_type=2)

    assert {case.document_id for case in cases} == {"priced"}


def test_a_type_is_injected_only_on_lines_carrying_its_cells() -> None:
    cases = injected(
        single((NO_MONEY, AMOUNTS), seed=1, clean=0, per_type=4), "price variance"
    )

    assert {case.document_id for case in cases} == {"amounts"}
    assert all(
        truth.place == Place("po line", 0) or truth.place == Place("po line", 1)
        for case in cases
        for truth in case.truth
    )


def test_a_pool_with_no_eligible_seed_injects_nothing() -> None:
    """The type then reads with n 0 in the report rather than failing it."""
    cases = single((NO_MONEY, NO_LINES), seed=1, clean=2, per_type=1)

    assert injected(cases) == []


def test_a_pool_whose_values_cannot_reach_a_band_is_an_error() -> None:
    """A cent-sized value has no near band: the cent floor swallows it."""
    tiny = seed("tiny", line(description="Washer", amount_gross="0.03"))

    with pytest.raises(GeneratorError, match="near price variance"):
        single((tiny,), seed=1, clean=0, per_type=1)


# Quantities

SHIPPED = seed(
    "shipped",
    line(description="Cable reel, 25 m", quantity="10", amount_net="900,00"),
    line(description="Junction box", quantity=["3", "4"], amount_net="420,00"),
)


def seeded_quantity(position: int) -> Decimal:
    """The highest quantity the seed line lists."""
    return max(
        Decimal(each) for each in SHIPPED.invoice.lines[position]["line_item_quantity"]
    )


def received(case: Case, position: int) -> tuple[str, ...]:
    (quantity,) = [
        each.cells["line_item_quantity"]
        for each in case.receipt.lines
        if each.po_line == position
    ]
    return tuple(quantity)


def ordered(case: Case, position: int) -> tuple[str, ...]:
    return tuple(case.purchase_order.lines[position]["line_item_quantity"])


def test_a_short_ship_lowers_the_receipt_quantity_and_leaves_the_po_as_seeded() -> None:
    cases = injected(single((SHIPPED,), seed=1, clean=0, per_type=6), "short-ship")

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        assert truth.place.kind == "po line"
        position = truth.place.line
        assert position is not None
        assert case.invoice == SHIPPED.invoice
        assert case.purchase_order == SHIPPED.invoice
        (lowered,) = received(case, position)
        assert quantity_band(seeded_quantity(position), Decimal(lowered)) == truth.band
        assert all(
            received(case, other) == ordered(case, other)
            for other in range(len(SHIPPED.invoice.lines))
            if other != position
        )


def test_an_over_ship_lowers_the_po_quantity_and_leaves_the_receipt_as_seeded() -> None:
    cases = injected(single((SHIPPED,), seed=1, clean=0, per_type=6), "over-ship")

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        position = truth.place.line
        assert position is not None
        assert case.invoice == SHIPPED.invoice
        seeded_line = SHIPPED.invoice.lines[position]
        po_line = case.purchase_order.lines[position]
        assert {**po_line, "line_item_quantity": seeded_line["line_item_quantity"]} == (
            seeded_line
        )
        (lowered,) = ordered(case, position)
        assert quantity_band(seeded_quantity(position), Decimal(lowered)) == truth.band
        assert all(
            received(case, each) == tuple(line_["line_item_quantity"])
            for each, line_ in enumerate(SHIPPED.invoice.lines)
        )


@pytest.mark.parametrize("type_", ["short-ship", "over-ship"])
def test_quantity_injections_are_one_unit_near_and_two_up_to_double_far(
    type_: DiscrepancyType,
) -> None:
    cases = injected(single((SHIPPED,), seed=1, clean=0, per_type=20), type_)

    bands = Counter(truth.band for case in cases for truth in case.truth)
    assert bands == {"near": 10, "far": 10}
    for case in cases:
        (truth,) = case.truth
        position = truth.place.line
        assert position is not None
        lowers = received if type_ == "short-ship" else ordered
        (lowered,) = lowers(case, position)
        overage = seeded_quantity(position) - Decimal(lowered)
        if truth.band == "near":
            assert overage == 1
        else:
            assert overage >= 2
            assert seeded_quantity(position) <= 2 * Decimal(lowered)


def test_a_quantity_is_injected_only_on_lines_carrying_one() -> None:
    freight = seed("freight", line(description="Freight", amount_gross="45.00"))
    cases = single((freight, SHIPPED), seed=1, clean=0, per_type=4)

    assert {
        case.document_id
        for type_ in ("short-ship", "over-ship")
        for case in injected(cases, type_)
    } == {"shipped"}


def test_a_pool_whose_quantities_cannot_reach_far_is_an_error() -> None:
    """Three units less two is one, under half of three: no far band."""
    few = seed("few", line(description="Washer", quantity="3"))

    with pytest.raises(GeneratorError, match="far short-ship"):
        single((few,), seed=1, clean=0, per_type=2)


# The pairing key


def test_the_pairing_key_names_the_invoice_line_every_po_line_answers() -> None:
    """One entry per purchase-order line, in order, on a case nothing was
    removed from or added to (#102)."""
    cases = single(POOL, seed=1, clean=3, per_type=0)

    for case in cases:
        assert case.pairing_key == tuple(range(len(case.invoice.lines)))


def test_an_extra_lines_invoice_line_is_in_no_pairing_key_entry() -> None:
    cases = injected(
        single((PRICED, AMOUNTS), seed=1, clean=0, per_type=6), "extra line"
    )

    assert len(cases) == 6
    for case in cases:
        removed = case.truth[0].place.line
        assert case.pairing_key == tuple(
            position
            for position in range(len(case.invoice.lines))
            if position != removed
        )


def test_the_po_line_a_missing_line_added_answers_no_invoice_line() -> None:
    cases = injected(single(POOL, seed=1, clean=0, per_type=6), "missing line")

    assert len(cases) == 6
    for case in cases:
        added = case.truth[0].place.line
        assert added is not None
        assert len(case.pairing_key) == len(case.purchase_order.lines)
        assert case.pairing_key[added] is None
        assert [each for each in case.pairing_key if each is not None] == list(
            range(len(case.invoice.lines))
        )


def test_a_pairing_key_that_does_not_name_every_po_line_is_refused() -> None:
    """A short key would be read as naming no line for the rest, and the
    crossed pairs it should have counted would go missing quietly (#102)."""
    (case,) = single((PRICED,), seed=1, clean=1, per_type=0)

    with pytest.raises(ValueError, match="purchase-order lines"):
        replace(case, pairing_key=case.pairing_key[:-1])


def test_the_pairing_key_is_what_the_matcher_pairs_on_a_clean_case() -> None:
    """The key is the generator's own record and the matcher never reads it,
    so on a case with nothing injected the two agree by themselves (#102)."""
    for case in single(POOL, seed=1, clean=4, per_type=0):
        result = match(case.invoice, case.purchase_order, case.receipt)
        assert {each.po_line: each.invoice_line for each in result.pairings} == {
            position: answers
            for position, answers in enumerate(case.pairing_key)
            if answers is not None
        }


# Extra line and missing line


def test_an_extra_line_removes_a_line_from_the_po_and_the_receipt() -> None:
    cases = injected(
        single((PRICED, AMOUNTS), seed=1, clean=0, per_type=6), "extra line"
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
    cases = injected(single(POOL, seed=1, clean=0, per_type=6), "missing line")

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


def test_a_missing_line_is_never_a_donor_line_with_values_only() -> None:
    """A donor line with no code and no description meets no floor, only the
    value tiebreak, so it is not the line added (#77)."""
    nameless = seed("nameless", line(amount_gross="12.00", quantity="1"))
    named = seed("named", line(description="Crate", amount_gross="40.00"))

    cases = single((PRICED, nameless, named), seed=1, clean=0, per_type=6)

    added = [
        case.purchase_order.lines[case.truth[0].place.line or 0]
        for case in injected(cases, "missing line")
    ]
    assert added
    assert nameless.invoice.lines[0] not in added
    assert all(
        "line_item_code" in each or "line_item_description" in each for each in added
    )


def test_a_missing_line_needs_another_seed_in_the_pool() -> None:
    """A pool of one seed has no other line to add: n 0, not an error."""
    cases = single((PRICED,), seed=1, clean=0, per_type=2)

    types = {case.truth[0].type for case in cases}
    assert "missing line" not in types
    assert "extra line" in types


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

    cases = single((doubled, donor), seed=1, clean=0, per_type=6)

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

    cases = single((dated, positioned), seed=1, clean=0, per_type=6)

    assert {case.truth[0].place for case in injected(cases, "extra line")} == {
        Place("invoice line", 1)
    }
    for case in injected(cases, "missing line"):
        added = case.truth[0].place.line
        assert added is not None
        assert "line_item_amount_gross" in case.purchase_order.lines[added]


# Unit variant

COUNTED = seed(
    "counted",
    line(
        description="Copier paper",
        units_of_measure="BOX",
        quantity="4",
        unit_price_gross="30.00",
        amount_gross="120.00",
    ),
    line(description="Stapler", units_of_measure=["ea", "Box"], quantity="1,5"),
    line(description="Freight", quantity="1", amount_gross="12.00"),
)


RECOUNTED = (
    "line_item_units_of_measure",
    "line_item_quantity",
    "line_item_unit_price_gross",
)
"""What a unit variant changes on a purchase-order line."""


def units(cells: FieldValues) -> set[str]:
    return {normalize_text(text) for text in cells["line_item_units_of_measure"]}


def number(cells: FieldValues, fieldtype: str) -> Decimal:
    (text,) = cells[fieldtype]
    value = read_number(text)
    assert value is not None
    return value


def but_recounted(cells: FieldValues) -> FieldValuesDict:
    return {
        fieldtype: tuple(texts)
        for fieldtype, texts in cells.items()
        if fieldtype not in RECOUNTED
    }


def test_a_unit_variant_counts_the_po_line_in_another_unit_and_keeps_the_amount() -> (
    None
):
    cases = injected(single((COUNTED,), seed=1, clean=0, per_type=10), "unit variant")

    assert len(cases) == 10
    for case in cases:
        (truth,) = case.truth
        assert truth.place.kind == "po line"
        assert truth.band is None
        position = truth.place.line
        assert position in (0, 1)
        assert case.invoice == COUNTED.invoice
        seeded = COUNTED.invoice.lines[position]
        po_line = case.purchase_order.lines[position]
        assert not units(po_line) & units(seeded)
        factor = number(po_line, "line_item_quantity") / number(
            seeded, "line_item_quantity"
        )
        assert factor == int(factor) and factor >= 2
        if position == 0:
            # 30.00 over the factor, to four places: the amount still agrees.
            unit_price = number(po_line, "line_item_unit_price_gross")
            assert abs(unit_price * factor - Decimal("30.00")) <= factor / 20000
        assert but_recounted(po_line) == but_recounted(seeded)
        assert [
            each
            for other, each in enumerate(case.purchase_order.lines)
            if other != position
        ] == [
            each
            for other, each in enumerate(COUNTED.invoice.lines)
            if other != position
        ]
        (receipt_line,) = [
            each.cells for each in case.receipt.lines if each.po_line == position
        ]
        assert units(receipt_line) == units(po_line)
        assert receipt_line["line_item_quantity"] == po_line["line_item_quantity"]


@pytest.mark.parametrize(
    "unreadable",
    [
        {"quantity": "two"},
        {"quantity": "2", "unit_price_gross": "30.00", "unit_price_net": "n/a"},
    ],
)
def test_a_line_that_cannot_be_recounted_carries_no_unit_variant(
    unreadable: dict[str, str],
) -> None:
    """A quantity or unit price the normalizer cannot read could not be
    scaled, and the purchase order's amount would stop agreeing with them."""
    garbled = seed(
        "garbled",
        line(
            description="Copier paper",
            units_of_measure="BOX",
            amount_gross="60.00",
            **unreadable,
        ),
    )

    assert documents_carrying((garbled,), "unit variant") == 0


def test_only_lines_labeling_a_unit_carry_a_unit_variant() -> None:
    cases = single((COUNTED, NO_MONEY), seed=1, clean=0, per_type=6)

    assert {case.document_id for case in injected(cases, "unit variant")} == {"counted"}
    assert documents_carrying((COUNTED, NO_MONEY), "unit variant") == 1


# Tax mismatch


def test_a_tax_mismatch_lowers_the_po_header_tax_and_nothing_else() -> None:
    cases = injected(single((PRICED,), seed=1, clean=0, per_type=6), "tax mismatch")

    assert len(cases) == 6
    for case in cases:
        (truth,) = case.truth
        assert truth.place == Place("header")
        assert case.purchase_order.lines == PRICED.invoice.lines
        header = dict(case.purchase_order.header)
        lowered = read_number(header.pop("amount_total_tax")[0])
        seeded = dict(PRICED.invoice.header)
        assert lowered is not None
        assert band(TAX, Decimal(seeded.pop("amount_total_tax")[0]), lowered) == (
            truth.band
        )
        assert header == seeded


def test_tax_mismatches_draw_near_and_far_half_and_half() -> None:
    cases = injected(single(POOL, seed=1, clean=0, per_type=10), "tax mismatch")

    assert Counter(truth.band for case in cases for truth in case.truth) == {
        "near": 5,
        "far": 5,
    }


def test_only_seeds_labeling_a_header_tax_carry_a_tax_mismatch() -> None:
    cases = injected(single(POOL, seed=1, clean=0, per_type=4), "tax mismatch")

    assert {case.document_id for case in cases} == {"priced"}
    assert documents_carrying(POOL, "tax mismatch") == 1


@pytest.mark.parametrize("tax", ["0.00", "n/a"])
def test_a_header_tax_that_is_zero_or_unreadable_is_not_eligible(tax: str) -> None:
    untaxed = seed(
        "untaxed",
        line(description="Hex key set", amount_gross="45.00"),
        amount_total_tax=tax,
    )

    assert documents_carrying((untaxed,), "tax mismatch") == 0
    assert (
        injected(single((untaxed,), seed=1, clean=0, per_type=2), "tax mismatch") == []
    )


def test_a_po_carries_one_header_tax_the_highest_the_seed_lists() -> None:
    """A labeled zero-rate row beside the total is not a second tax: every PO,
    clean or not, carries the total alone, so it never disagrees with itself."""
    listed = Seed(
        "listed",
        Record(
            header={"amount_total_tax": ("0.00", "8.55")},
            lines=(line(description="Hex key set", amount_gross="45.00"),),
        ),
    )

    cases = single((listed,), seed=1, clean=1, per_type=2)

    assert [case.purchase_order.header for case in cases[:1]] == [
        {"amount_total_tax": ("8.55",)}
    ]
    assert all(
        case.purchase_order.header == {"amount_total_tax": ("8.55",)}
        for case in injected(cases, "price variance")
    )
    assert all(case.purchase_order.lines == listed.invoice.lines for case in cases[:1])


# Mixed cases

STOCKED = seed(
    "stocked",
    line(code="CP-500", description="Copier paper", quantity="8", amount_gross="96.00"),
    line(code="ST-120", description="Stapler", quantity="6", unit_price_gross="12.50"),
    line(code="HL-310", description="Hole punch", quantity="5", amount_gross="45.00"),
    line(
        code="TP-044", description="Packing tape", quantity="12", amount_gross="30.00"
    ),
    line(
        code="LB-900",
        description="Label printer",
        units_of_measure="EA",
        quantity="4",
        unit_price_gross="120.00",
        amount_gross="480.00",
    ),
    amount_total_tax="42.00",
)
DONOR = seed(
    "donor",
    line(code="ZX-7", description="Mop bucket", quantity="3", amount_gross="54.00"),
    line(code="QW-2", description="Floor wax", quantity="9", amount_gross="81.00"),
)
MIXED = (STOCKED, DONOR)


def test_a_case_carries_one_to_three_discrepancies_never_two_on_one_line() -> None:
    cases = generate(MIXED, seed=1, clean=0, per_type=20)

    assert {len(case.truth) for case in cases} == {1, 2, 3}
    for case in cases:
        places = [truth.place for truth in case.truth]
        assert len(set(places)) == len(places)
        assert [truth.type for truth in case.truth].count("tax mismatch") <= 1
        for place in places:
            if place.kind == "po line":
                assert place.line is not None
                assert place.line < len(case.purchase_order.lines)


def test_every_type_reaches_its_count_with_near_and_far_half_and_half() -> None:
    cases = generate(MIXED, seed=1, clean=0, per_type=20)

    found = Counter(truth.type for case in cases for truth in case.truth)
    assert all(found[type_] >= 20 for type_ in INJECTED_TYPES)
    for type_ in ("price variance", "short-ship", "over-ship", "tax mismatch"):
        bands = Counter(
            truth.band for case in cases for truth in case.truth if truth.type == type_
        )
        assert set(bands) == {"near", "far"}
        assert abs(bands["near"] - bands["far"]) <= 1


def test_most_one_keeps_every_case_to_its_own_type() -> None:
    cases = generate(MIXED, seed=1, clean=0, per_type=4, most=1)

    assert [len(case.truth) for case in cases] == [1] * 4 * len(INJECTED_TYPES)


def test_on_named_lines_the_matcher_finds_every_truth_and_nothing_else() -> None:
    """Each place is where the injection landed once an extra line or a missing
    line has shifted the purchase order, and no hard negative fires."""
    cases = generate(MIXED, seed=1, clean=20, per_type=20)

    for case in cases:
        result = match(case.invoice, case.purchase_order, case.receipt)
        assert {(each.type, each.place) for each in result.findings} == {
            (each.type, each.place) for each in case.truth
        }


# Hard negatives


def hard_negatives(
    cases: Sequence[Case], kind: HardNegativeKind
) -> list[tuple[Case, HardNegative]]:
    return [
        (case, each)
        for case in cases
        for each in case.hard_negatives
        if each.kind == kind
    ]


def cell_number(cells: FieldValues, cell: Cell) -> Decimal:
    """The highest value the cell lists, the one the matcher compares."""
    values = cell_values(cells, cell)
    assert values is not None
    numbers = [read_number(text) for text in values.texts]
    return max(each for each in numbers if each is not None)


def sides(case: Case, negative: HardNegative) -> tuple[Decimal, Decimal]:
    """The invoice's and the purchase order's value on the hard negative's cell."""
    assert negative.place.line is not None
    return (
        cell_number(case.invoice.lines[negative.invoice_line], negative.cell),
        cell_number(case.purchase_order.lines[negative.place.line], negative.cell),
    )


def test_hard_negatives_sit_on_clean_lines_in_every_kind_of_case() -> None:
    cases = generate(MIXED, seed=1, clean=20, per_type=20)

    assert {each.kind for case in cases for each in case.hard_negatives} == set(
        HARD_NEGATIVE_KINDS
    )
    assert any(case.hard_negatives for case in cases if case.is_clean)
    assert any(case.hard_negatives for case in cases if not case.is_clean)
    for case in cases:
        negative_places = [each.place for each in case.hard_negatives]
        assert len(set(negative_places)) == len(negative_places)
        assert not set(negative_places) & {truth.place for truth in case.truth}


def test_a_hard_negative_line_differs_from_its_seed_only_in_its_cell() -> None:
    cases = generate((STOCKED,), seed=1, clean=20, per_type=0)

    assert any(case.hard_negatives for case in cases)
    for case in cases:
        for negative in case.hard_negatives:
            assert negative.place == Place("po line", negative.invoice_line)
            po_line = case.purchase_order.lines[negative.invoice_line]
            seeded = case.invoice.lines[negative.invoice_line]
            changed = {
                fieldtype
                for fieldtype in {*po_line, *seeded}
                if po_line.get(fieldtype) != seeded.get(fieldtype)
            }
            assert len(changed) == 1
            assert changed <= set(CELL_FIELDTYPES[negative.cell])


def test_rounding_drift_is_one_cent_over_or_under_on_the_compared_cell() -> None:
    drifts = hard_negatives(
        generate(MIXED, seed=1, clean=40, per_type=0), "rounding drift"
    )

    directions = set()
    for case, negative in drifts:
        seeded = case.invoice.lines[negative.invoice_line]
        compared: Cell = "unit price" if cell_values(seeded, "unit price") else "amount"
        assert negative.cell == compared
        billed, ordered = sides(case, negative)
        assert abs(billed - ordered) == Decimal("0.01")
        directions.add(billed > ordered)
    assert directions == {True, False}


def test_just_inside_lands_within_90_to_100_percent_of_the_margin() -> None:
    insides = hard_negatives(
        generate(MIXED, seed=1, clean=40, per_type=0), "just inside"
    )

    assert insides
    for case, negative in insides:
        assert negative.cell != "quantity"
        billed, ordered = sides(case, negative)
        margin = PRICE.margin(ordered)
        assert Decimal("0.9") * margin <= billed - ordered <= margin


def test_just_inside_is_never_generated_on_a_value_under_one() -> None:
    small = seed(
        "small",
        line(description="Washer", quantity="40", unit_price_gross="0.95"),
        line(description="Grommet", quantity="30", amount_gross="0.60"),
    )

    cases = generate((small,), seed=1, clean=40, per_type=0)

    assert hard_negatives(cases, "just inside") == []
    assert hard_negatives(cases, "rounding drift")


def test_billed_below_raises_the_po_price_or_quantity_and_the_receipt_follows() -> None:
    belows = hard_negatives(
        generate(MIXED, seed=1, clean=40, per_type=0), "billed below"
    )

    assert {negative.cell for _, negative in belows} == {
        "quantity",
        "unit price",
        "amount",
    }
    for case, negative in belows:
        billed, ordered = sides(case, negative)
        assert billed < ordered
        if negative.cell == "quantity":
            (receipt_line,) = [
                each.cells
                for each in case.receipt.lines
                if each.po_line == negative.place.line
            ]
            assert cell_number(receipt_line, "quantity") == ordered


# Lines with nothing else to pair on


def test_no_edit_changes_the_only_cell_a_nameless_line_pairs_on() -> None:
    """Such a line could not be told from any other once changed, so neither a
    discrepancy nor a hard negative is put on it: the truth could not be
    scored. A cell the edit leaves as labeled still pairs it."""
    nameless = seed(
        "nameless",
        line(amount_gross="75.00"),
        line(quantity="6", amount_gross="84.00"),
        line(units_of_measure="BOX", quantity="3", unit_price_gross="14.00"),
    )

    cases = generate((nameless, DONOR), seed=1, clean=40, per_type=20)

    alone = nameless.invoice.lines[0]
    for case in cases:
        if case.document_id != "nameless":
            continue
        removed = Injected("extra line", Place("invoice line", 0), None)
        assert removed in case.truth or alone in case.purchase_order.lines
    assert documents_carrying((nameless,), "price variance") == 1
    assert documents_carrying((nameless,), "over-ship") == 1
    assert documents_carrying((nameless,), "unit variant") == 0
