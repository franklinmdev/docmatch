"""Tests for scoring match results against the generator's truth: seam 3.

Cases and results are built by hand, so what is pinned is how findings are
counted, not what the matcher does with the case's records.
"""

from dataclasses import replace
from decimal import Decimal

from docmatch.matching.generator import BANDED_TYPES, Case, HardNegative, Injected
from docmatch.matching.matcher import (
    Finding,
    MatchResult,
    NotCompared,
    Place,
    UnpairedFinding,
)
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.matching.scoring import TypeScore, score
from docmatch.matching.tolerances import PRICE, UNIT

EMPTY = Record(header={}, lines=())


def case(*truth: Injected) -> Case:
    return Case("made-up", EMPTY, EMPTY, ReceivingRecord(()), truth)


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


def test_recall_splits_into_near_the_edge_and_far_past_it() -> None:
    far = Injected("price variance", Place("po line", 1), "far")
    table = score([case(injected(0), far, injected(2))], [result(0, 1)])

    rows = {(each.type, each.band): (each.n, each.recall) for each in table.bands}
    assert rows["price variance", "near"] == (2, 0.5)
    assert rows["price variance", "far"] == (1, 1.0)
    assert {each.type for each in table.bands} == set(BANDED_TYPES)
    assert rows["tax mismatch", "far"] == (0, 1.0)
