"""Tests for scoring match results against the generator's truth: seam 3.

Cases and results are built by hand, so what is pinned is how findings are
counted, not what the matcher does with the case's records. The end-to-end
row's tests match a hand-built reading against a hand-built case, and pin
where its findings are placed and what a missing reading or the floor costs.
"""

from dataclasses import replace
from decimal import Decimal

from docmatch.matching.generator import BANDED_TYPES, Case, HardNegative, Injected
from docmatch.matching.matcher import (
    Finding,
    MatchResult,
    NotCompared,
    Pairing,
    Place,
    UnpairedFinding,
)
from docmatch.matching.records import ReceiptLine, ReceivingRecord, Record
from docmatch.matching.scoring import (
    CrossedPairs,
    FloorCost,
    Table,
    TypeScore,
    score,
    score_read,
)
from docmatch.matching.tolerances import PRICE, UNIT

EMPTY = Record(header={}, lines=())


def case(*truth: Injected) -> Case:
    return Case("made-up", EMPTY, EMPTY, ReceivingRecord(()), truth, ())


def injected(line: int) -> Injected:
    return Injected("price variance", Place("po line", line), "near")


def result(*lines: int) -> MatchResult:
    return MatchResult(
        pairings=(),
        findings=tuple(
            Finding(
                type="price variance",
                place=Place("po line", line),
                cell="amount",
                invoice=("110.00",),
                purchase_order=("100.00",),
                margin=Decimal("1.00"),
                tolerance=PRICE,
            )
            for line in lines
        ),
        not_compared=(),
    )


def price_variance(table_types: tuple[TypeScore, ...]) -> TypeScore:
    return next(each for each in table_types if each.type == "price variance")


def test_a_finding_is_a_hit_only_when_type_and_place_both_agree() -> None:
    table = score(
        [case(injected(1)), case(injected(1))],
        [result(1), result(0)],
    )

    row = price_variance(table.per_type)
    assert (row.hits, row.misses, row.false_alarms) == (1, 1, 1)
    assert row.n == 2
    assert row.precision == 0.5
    assert row.recall == 0.5


def test_the_clean_case_rate_counts_cases_with_any_finding() -> None:
    table = score(
        [case(), case(), case(), case(injected(0))],
        [result(0, 1), result(), result(), result(0)],
    )

    assert table.clean_cases == 3
    assert table.clean_false_positives == 1
    assert table.clean_false_positive_rate == 1 / 3


def test_findings_on_clean_cases_are_false_alarms_of_their_type() -> None:
    table = score([case()], [result(0, 1)])

    assert price_variance(table.per_type).false_alarms == 2
    assert table.overall.precision == 0.0


def test_a_type_nothing_was_injected_for_reads_with_n_zero() -> None:
    table = score([case(injected(0))], [result(0)])

    assert [each.n for each in table.per_type if each.type != "price variance"] == [
        0
    ] * 6
    assert table.overall.recall == 1.0


def test_any_other_finding_on_a_unit_variant_line_is_a_false_alarm() -> None:
    """The line's price and quantity are suppressed, so a price variance on it
    is wrong whatever its values say (#66)."""
    unit_variant = Finding(
        type="unit variant",
        place=Place("po line", 0),
        cell="unit",
        invoice=("BOX",),
        purchase_order=("EA",),
        margin=Decimal(0),
        tolerance=UNIT,
    )
    found = replace(result(0), findings=(unit_variant, *result(0).findings))

    table = score([case(Injected("unit variant", Place("po line", 0), None))], [found])

    rows = {each.type: each for each in table.per_type}
    assert (rows["unit variant"].hits, rows["unit variant"].false_alarms) == (1, 0)
    assert rows["price variance"].false_alarms == 1


def test_what_was_not_compared_counts_in_no_score() -> None:
    listed = replace(
        result(),
        not_compared=(
            NotCompared(Place("po line", 0), "unit price", ("n/a",), "unreadable"),
        ),
    )

    table = score([case()], [listed])

    assert table.clean_false_positives == 0
    assert all(each.false_alarms == 0 for each in table.per_type)


def test_the_rate_is_zero_when_there_is_no_clean_case() -> None:
    assert score([case(injected(0))], [result(0)]).clean_false_positive_rate == 0.0


def line(description: str, quantity: str, amount: str) -> dict[str, tuple[str, ...]]:
    return {
        "line_item_description": (description,),
        "line_item_quantity": (quantity,),
        "line_item_amount_gross": (amount,),
    }


HEX_KEYS = line("Hex key set", "3", "45.00")
WRENCH = line("Torque wrench", "1", "317.50")
GLOVES = line("Work gloves", "5", "50.00")


def labeled(*lines: dict[str, tuple[str, ...]]) -> Record:
    return Record(header={}, lines=tuple(lines))


def received(po: Record) -> ReceivingRecord:
    return ReceivingRecord(
        tuple(
            ReceiptLine(
                {
                    "line_item_quantity": each["line_item_quantity"],
                    "line_item_description": each["line_item_description"],
                },
                position,
            )
            for position, each in enumerate(po.lines)
        )
    )


def a_case(invoice: Record, po: Record, *truth: Injected) -> Case:
    """A case whose key names no invoice line, so nothing it pairs is counted
    as crossed; `keyed` writes one where a test reads the crossings."""
    return Case("made-up", invoice, po, received(po), truth, (None,) * len(po.lines))


def rows(table: Table) -> dict[str, TypeScore]:
    return {each.type: each for each in table.per_type}


def test_an_extra_line_on_a_reading_is_placed_on_the_labeled_line_it_reads() -> None:
    """The reading drops the first labeled line, so the extra line it bills
    sits at reading position 1 and labeled position 2: the line-item
    metric's own assignment maps it back, and it is a hit (#76)."""
    extra = a_case(
        labeled(HEX_KEYS, WRENCH, GLOVES),
        labeled(HEX_KEYS, WRENCH),
        Injected("extra line", Place("invoice line", 2), None),
    )

    scored = score_read([extra], {"made-up": labeled(WRENCH, GLOVES)})

    found = rows(scored.table)
    assert (found["extra line"].hits, found["extra line"].false_alarms) == (1, 0)
    # The dropped row leaves its purchase-order line unpaired: a false alarm.
    assert found["missing line"].false_alarms == 1


def test_a_reading_line_no_labeled_line_answers_is_a_false_alarm() -> None:
    """A line the reading made up has no labeled line to be placed on, so it
    can never be taken for the extra line the generator removed."""
    extra = a_case(
        labeled(HEX_KEYS, WRENCH),
        labeled(HEX_KEYS),
        Injected("extra line", Place("invoice line", 1), None),
    )
    made_up = line("Delivery", "2", "0.00")

    scored = score_read([extra], {"made-up": labeled(HEX_KEYS, made_up)})

    found = rows(scored.table)
    assert (found["extra line"].hits, found["extra line"].misses) == (0, 1)
    assert found["extra line"].false_alarms == 1


def test_a_document_with_no_reading_is_an_invoice_with_no_lines() -> None:
    """A failed or empty reading pays in misses and false alarms (#67)."""
    po = labeled(HEX_KEYS, WRENCH)
    short = a_case(
        labeled(HEX_KEYS, WRENCH),
        po,
        Injected("short-ship", Place("po line", 0), "near"),
    )

    scored = score_read([short, a_case(labeled(HEX_KEYS, WRENCH), po)], {})

    found = rows(scored.table)
    assert found["short-ship"].misses == 1
    assert found["missing line"].false_alarms == 4
    assert scored.table.clean_false_positives == 1


def test_the_floor_costs_the_misread_lines_it_leaves_unpaired() -> None:
    """Over one clean case per document: a line read with its description
    garbled below the floor is still the line-item metric's pair of its
    labeled line, so the floor cost it; a line the reading made up is not
    the floor's cost."""
    clean = a_case(labeled(HEX_KEYS, WRENCH), labeled(HEX_KEYS, WRENCH))
    garbled = line("Spanner", "1", "317.50")
    made_up = line("Delivery", "1", "0.00")

    reading = labeled(HEX_KEYS, garbled, made_up)

    scored = score_read([clean, clean], {"made-up": reading})

    assert scored.floor_cost == FloorCost(paired=2, unpaired=1)


# The diagnostic table


def test_a_false_alarm_on_a_hard_negative_counts_toward_its_kind() -> None:
    """An extra line counts where its invoice line is the hard negative's."""
    tempted = replace(
        case(),
        hard_negatives=(
            HardNegative("rounding drift", Place("po line", 0), "amount", 0),
            HardNegative("billed below", Place("po line", 1), "quantity", 2),
            HardNegative("rounding drift", Place("po line", 3), "unit price", 4),
        ),
    )
    extra = UnpairedFinding("extra line", Place("invoice line", 2), None)
    found = replace(result(0, 2), findings=(*result(0, 2).findings, extra))

    table = score([tempted], [found])

    assert [
        (each.kind, each.placed, each.false_alarms) for each in table.hard_negatives
    ] == [
        ("rounding drift", 2, 1),
        ("just inside", 0, 0),
        ("billed below", 1, 1),
    ]
    assert table.false_alarms_elsewhere == 1


def paired(*pairs: tuple[int, int]) -> MatchResult:
    """A result that found nothing and paired the lines given, invoice first."""
    return replace(
        result(),
        pairings=tuple(
            Pairing(invoice_line=invoice, po_line=po, code=None, description=1.0)
            for invoice, po in pairs
        ),
    )


def keyed(invoice: Record, *key: int | None) -> Case:
    """A case with the pairing key given. The scorer reads the invoice's lines
    only to ask whether a crossed pair's two lines are tellable apart."""
    return replace(a_case(invoice, invoice), pairing_key=key)


THREE = labeled(HEX_KEYS, WRENCH, GLOVES)


def test_a_line_paired_with_another_partner_than_the_keys_is_crossed() -> None:
    table = score([keyed(THREE, 0, 1, 2)], [paired((1, 0), (0, 1), (2, 2))])

    assert table.crossed_pairs == CrossedPairs(keyed=3, crossed=2, alike=0)


def test_a_crossing_between_lines_nothing_tells_apart_is_counted_apart() -> None:
    """The two lines carry the same description, quantity and amount, so no
    tiebreak could have preferred one of them: the crossing is counted, and
    counted again as one nothing pairing reads separates (#102)."""
    twins = labeled(HEX_KEYS, dict(HEX_KEYS), GLOVES)

    table = score([keyed(twins, 0, 1, 2)], [paired((1, 0), (0, 1), (2, 2))])

    assert table.crossed_pairs == CrossedPairs(keyed=3, crossed=2, alike=2)


def test_a_po_line_the_key_names_no_invoice_line_for_is_not_counted() -> None:
    """A line a missing line added answers a line of another seed, so its
    partner is neither the right one nor a crossed one (#102)."""
    table = score([keyed(THREE, 0, None, 1)], [paired((0, 0), (2, 1), (1, 2))])

    assert table.crossed_pairs == CrossedPairs(keyed=2, crossed=0, alike=0)


def test_a_reading_is_counted_against_the_key_at_the_labeled_line_it_reads() -> None:
    """The reading drops the first labeled line, so every later row sits one
    position early; the pairing is scored at the labeled line the line-item
    metric's assignment pairs it with, as an extra line's place is (#102)."""
    scored = score_read([keyed(THREE, 0, 1, 2)], {"made-up": labeled(WRENCH, GLOVES)})

    assert scored.table.crossed_pairs == CrossedPairs(keyed=2, crossed=0, alike=0)


def test_recall_splits_into_near_the_edge_and_far_past_it() -> None:
    far = Injected("price variance", Place("po line", 1), "far")
    table = score([case(injected(0), far, injected(2))], [result(0, 1)])

    rows = {(each.type, each.band): (each.n, each.recall) for each in table.bands}
    assert rows["price variance", "near"] == (2, 0.5)
    assert rows["price variance", "far"] == (1, 1.0)
    assert {each.type for each in table.bands} == set(BANDED_TYPES)
    assert rows["tax mismatch", "far"] == (0, 1.0)
