"""Tests for the trigram arm over a catalog in a real Postgres: seam 2 of the
Phase 3 spec. Skipped, not failed, when no database answers. Every
description is made up, and the catalogs are built to tie."""

import string

from docmatch.resolution.arms import FETCH, TOP, Answer, ordered, trigram
from docmatch.resolution.catalog import Entry, mint
from docmatch.resolution.store import Store


def entries(*descriptions: str) -> list[Entry]:
    return [Entry(mint(each), each) for each in descriptions]


def widgets(count: int) -> list[Entry]:
    """`count` descriptions at the same trigram distance from `widget`: one
    letter each after it, so every one shares its trigrams and adds two."""
    return entries(*(f"widget {letter}" for letter in string.ascii_lowercase[:count]))


def test_ordered_ranks_by_score_then_sku_and_cuts_to_five() -> None:
    fetched = ordered(
        [("SKU-c", 0.5), ("SKU-a", 0.5), ("SKU-f", 0.1), ("SKU-b", 0.5), ("SKU-e", 0.9)]
        + [("SKU-d", 0.5)]
    )

    assert fetched.answers == (
        Answer("SKU-f", 0.1),
        Answer("SKU-a", 0.5),
        Answer("SKU-b", 0.5),
        Answer("SKU-c", 0.5),
        Answer("SKU-d", 0.5),
    )
    assert fetched.fetched == 6
    assert fetched.boundary == 0.9


def test_ordered_reports_a_tie_at_rank_1_and_not_one_lower_down() -> None:
    assert ordered([("SKU-b", 0.2), ("SKU-a", 0.2), ("SKU-c", 0.3)]).tied_at_1
    assert not ordered([("SKU-a", 0.1), ("SKU-b", 0.2), ("SKU-c", 0.2)]).tied_at_1
    assert not ordered([("SKU-a", 0.1)]).tied_at_1


def test_the_over_fetch_check_is_skipped_below_a_full_fetch() -> None:
    assert ordered([("SKU-a", 0.5)] * (FETCH - 1)).boundary_equals_cut is None
    assert ordered([]).boundary_equals_cut is None
    assert ordered([]).answers == ()


def test_the_over_fetch_check_fires_when_a_tie_group_reaches_the_boundary() -> None:
    tied = [(f"SKU-{index:02d}", 0.5) for index in range(FETCH)]
    clear = [(f"SKU-{index:02d}", index / 100) for index in range(FETCH)]

    assert ordered(tied).boundary_equals_cut is True
    assert ordered(clear).boundary_equals_cut is False


def test_trigram_returns_five_ordered_by_distance_then_sku(store: Store) -> None:
    catalog = entries("blue widget", "red widget", "green gadget", "widget")
    catalog += widgets(4)
    store.rebuild(catalog)

    fetched = trigram(store, "widget")

    assert len(fetched.answers) == TOP
    assert fetched.answers[0].sku == mint("widget")
    assert fetched.answers[0].score == 0.0
    scores = [each.score for each in fetched.answers]
    assert scores == sorted(scores)
    for one, other in zip(fetched.answers, fetched.answers[1:], strict=False):
        assert (one.score, one.sku) < (other.score, other.sku)
    assert not fetched.tied_at_1
    assert fetched.fetched == len(catalog)


def test_trigram_breaks_ties_by_sku_whatever_order_the_index_returned(
    store: Store,
) -> None:
    """Eight entries at one distance from the query: the five are the five
    smallest SKUs, inserted in either order."""
    tied = widgets(8)
    expected = sorted(each.sku for each in tied)[:TOP]

    store.rebuild(tied)
    forwards = trigram(store, "widget")
    store.rebuild(list(reversed(tied)))
    backwards = trigram(store, "widget")

    assert [each.sku for each in forwards.answers] == expected
    assert forwards == backwards
    assert forwards.tied_at_1
    assert len({each.score for each in forwards.answers}) == 1


def test_trigram_reports_a_tie_group_that_reaches_the_fetched_boundary(
    store: Store,
) -> None:
    """Twenty-six entries at one distance: the index returns 25 of them, the
    boundary's distance equals the cut's, and the run says so rather than
    trusting the index's own order."""
    store.rebuild(widgets(26))

    fetched = trigram(store, "widget")

    assert fetched.fetched == FETCH
    assert fetched.boundary_equals_cut is True


def test_trigram_over_fetch_is_enough_when_the_cut_sits_clear_of_the_boundary(
    store: Store,
) -> None:
    close = widgets(4)
    far = entries(*(f"gadget number {index} of many" for index in range(30)))
    store.rebuild(close + far)

    fetched = trigram(store, "widget")

    assert fetched.fetched == FETCH
    assert fetched.boundary_equals_cut is False
    assert {each.sku for each in fetched.answers} > {each.sku for each in close}


def test_trigram_answers_fewer_than_five_over_a_smaller_catalog(store: Store) -> None:
    store.rebuild(entries("blue widget", "red widget"))

    fetched = trigram(store, "widget")

    assert len(fetched.answers) == 2
    assert fetched.boundary_equals_cut is None
