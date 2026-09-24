"""Tests for a line resolved at the operating threshold: the decision pure,
and the hybrid behind it over a catalog in a real Postgres, skipped when none
answers, with the fake embedder."""

from docmatch.resolution.arms import RRF_K, Answer, Fetched
from docmatch.resolution.catalog import Entry, mint
from docmatch.resolution.conftest import BucketEmbedder
from docmatch.resolution.operating import (
    KEEP,
    OPERATING_THRESHOLD,
    Resolved,
    at_threshold,
    description_of,
    resolve_line,
)
from docmatch.resolution.store import Store


def top(score: float) -> Fetched:
    return Fetched((Answer("SKU-a", score), Answer("SKU-b", score / 2)), "short")


def test_a_line_at_the_threshold_carries_its_top_1_sku_and_score() -> None:
    assert at_threshold(top(OPERATING_THRESHOLD)) == Resolved(
        sku="SKU-a", score=OPERATING_THRESHOLD
    )


def test_a_line_below_the_threshold_has_no_entry_and_keeps_its_score() -> None:
    below = OPERATING_THRESHOLD * 0.99

    assert at_threshold(top(below)) == Resolved(sku=None, score=below)


def test_a_line_the_hybrid_answered_with_nothing_has_no_entry_and_no_score() -> None:
    assert at_threshold(Fetched((), "short")) == Resolved(sku=None, score=None)


def test_a_line_is_queried_by_its_description_normalized() -> None:
    assert description_of({"line_item_description": ("  Cable REEL, 25 m",)}) == (
        "cable reel, 25 m"
    )


def test_a_description_read_in_parts_is_queried_as_one_text_in_reading_order() -> None:
    """A query is one description (#119), so the parts join in the order read."""
    line = {"line_item_description": ("Cable reel,", "", "25 m")}

    assert description_of(line) == "cable reel, 25 m"


def test_a_line_with_no_description_has_nothing_to_resolve() -> None:
    assert description_of({"line_item_quantity": ("2",)}) is None
    assert description_of({"line_item_description": (" ",)}) is None


def test_the_threshold_keeps_the_share_151_named() -> None:
    assert KEEP == 0.95


def test_the_threshold_is_a_score_the_hybrid_can_give() -> None:
    """A fused score is a sum of reciprocal ranks, one term per half the SKU
    is in; the constant is one of them, not a rounded reading of one."""
    reachable = {1 / (RRF_K + rank) for rank in range(1, 101)} | {
        1 / (RRF_K + one) + 1 / (RRF_K + other)
        for one in range(1, 101)
        for other in range(1, 101)
    }

    assert OPERATING_THRESHOLD in reachable


def test_resolve_line_names_the_entry_a_description_equals(
    store: Store, embedder: BucketEmbedder
) -> None:
    """An exact description is first in both halves, the highest fused score
    there is, so it carries its SKU at any threshold the procedure can give."""
    descriptions = ("widget a", "widget b", "gadget c")
    store.rebuild([Entry(mint(each), each) for each in descriptions], embedder)

    assert resolve_line(store, embedder, "widget b") == Resolved(
        sku=mint("widget b"), score=2 / (RRF_K + 1)
    )
