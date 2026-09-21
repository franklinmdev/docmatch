"""Tests for the measurement pass on a fake arm, and one run over a real
Postgres: seam 2 of the Phase 3 spec. Every value is made up."""

import pytest

from docmatch.resolution.arms import FETCH, Answer, Fetched, ordered
from docmatch.resolution.catalog import Entry, Query, build_catalog, mint
from docmatch.resolution.run import (
    EXACT_WEIGHT,
    ArmResult,
    KindScore,
    OverFetch,
    headline,
    measure,
    resolve,
)
from docmatch.resolution.store import StoreError
from docmatch.resolution.test_catalog import described


def answering(*skus: str) -> Fetched:
    """A fetch whose five come in the order given, at distinct scores."""
    return ordered([(sku, index / 10) for index, sku in enumerate(skus)])


def test_measure_scores_top_1_and_top_5_against_the_query_truth() -> None:
    answers = {
        "one": answering("SKU-1", "SKU-2"),
        "two": answering("SKU-9", "SKU-2"),
        "three": answering("SKU-9", "SKU-8", "SKU-7", "SKU-6", "SKU-5", "SKU-3"),
        "four": Fetched((), 0, None),
    }
    queries = [
        Query("one", "SKU-1", "exact"),
        Query("two", "SKU-2", "exact"),
        Query("three", "SKU-3", "exact"),
        Query("four", "SKU-4", "exact"),
    ]

    result = measure("fake", lambda text: answers[text], queries)

    assert result.kinds == (KindScore("exact", 4, 1, 2),)
    assert result.top1 == 0.25
    assert result.top5 == 0.5
    assert result.queries == 4


def test_measure_counts_ties_at_rank_1_and_what_the_over_fetch_check_found() -> None:
    tied = ordered([(f"SKU-{index:02d}", 0.5) for index in range(FETCH)])
    clear = ordered([(f"SKU-{index:02d}", index / 100) for index in range(FETCH)])
    short = ordered([("SKU-a", 0.1), ("SKU-b", 0.1)])
    answers = {"tied": tied, "clear": clear, "short": short}
    queries = [Query(text, "SKU-x", "exact") for text in answers]

    result = measure("fake", lambda text: answers[text], queries)

    assert result.tied_at_1 == 2
    assert result.tie_rate == pytest.approx(2 / 3)
    assert result.over_fetch == OverFetch(full=2, equal=1, short=1)


def test_measure_times_the_arm_itself_and_discards_the_warmup_pass() -> None:
    calls: list[str] = []

    def slow_first(text: str) -> Fetched:
        calls.append(text)
        return answering("SKU-1")

    result = measure("fake", slow_first, [Query("one", "SKU-1", "exact")] * 3)

    assert calls == ["one"] * 6, "every query once discarded, once measured"
    assert 0 <= result.p50_ms <= result.p95_ms


def test_the_headline_weights_exact_and_noisy_or_takes_the_one_present() -> None:
    assert pytest.approx(2 / 3) == EXACT_WEIGHT
    assert headline(0.9, 0.6) == pytest.approx(0.8)
    assert headline(0.9, None) == pytest.approx(0.9)
    assert headline(None, 0.6) == pytest.approx(0.6)
    assert headline(None, None) is None


def test_an_arm_over_no_queries_has_no_rates() -> None:
    result = ArmResult(
        "fake", (KindScore("exact", 0, 0, 0),), 0, 0, 0.0, 0.0, OverFetch(0, 0, 0)
    )

    assert result.top1 is None
    assert result.top5 is None
    assert result.tie_rate is None


def test_resolve_over_an_empty_catalog_touches_no_database() -> None:
    catalog = build_catalog([described("Blue widget"), described("Red widget")])

    result = resolve(catalog, "postgresql://localhost:1/nothing")

    assert result.measured is None
    assert [(each.kind, each.queries) for each in result.kinds] == [("exact", 0)]
    assert result.catalog.entries == 0


def test_resolve_answers_every_exact_query_with_its_own_entry_first(
    database_url: str,
) -> None:
    catalog = build_catalog(
        [
            described("Blue widget", "Red widget", "Green gadget"),
            described("Blue widget", "Red widget", "Green gadget"),
        ]
    )

    result = resolve(catalog, database_url, schema="resolution_test")

    assert result.measured is not None
    (arm,) = result.measured.arms
    assert arm.name == "trigram"
    assert arm.kinds == (KindScore("exact", 3, 3, 3),)
    assert arm.top1 == 1.0
    assert arm.over_fetch == OverFetch(full=0, equal=0, short=3)
    assert result.measured.versions.pg_trgm
    assert result.measured.machine.logical_cpus >= 1


def test_a_statement_the_server_refuses_is_reported_not_raised(
    database_url: str,
) -> None:
    """`pg_catalog` cannot be dropped, so a run in it is refused after the
    connection, which is a report and not a traceback."""
    catalog = build_catalog([described("Blue widget"), described("Blue widget")])

    with pytest.raises(StoreError, match="the database refused the run"):
        resolve(catalog, database_url, schema="pg_catalog")


def test_an_answer_carries_the_sku_and_the_score_the_arm_gave() -> None:
    assert answering("SKU-1").answers == (Answer("SKU-1", 0.0),)
    assert Entry(mint("x"), "x").sku == mint("x")
