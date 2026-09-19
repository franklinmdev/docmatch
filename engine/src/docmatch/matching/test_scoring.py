"""Tests for scoring match results against the generator's truth: seam 3.

Cases and results are built by hand, so what is pinned is how findings are
counted, not what the matcher does with the case's records.
"""

from decimal import Decimal

from docmatch.matching.generator import Case, Injected
from docmatch.matching.matcher import Finding, MatchResult, Place
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.matching.scoring import TypeScore, score
from docmatch.matching.tolerances import PRICE

EMPTY = Record(header={}, lines=())


def case(*truth: Injected) -> Case:
    return Case("made-up", EMPTY, EMPTY, ReceivingRecord(()), truth)


def injected(line: int) -> Injected:
    return Injected("price variance", Place("po line", line), "near")


def result(*lines: int) -> MatchResult:
    return MatchResult(
        pairings=(),
        unpaired_invoice=(),
        unpaired_po=(),
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


def test_the_rate_is_zero_when_there_is_no_clean_case() -> None:
    assert score([case(injected(0))], [result(0)]).clean_false_positive_rate == 0.0
