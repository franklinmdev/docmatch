"""Tests for the arms over a catalog in a real Postgres: seam 2 of the Phase 3
spec. Skipped, not failed, when no database answers. Every description is
made up, the catalogs are built to tie, and the embedder is the fake."""

import string

from docmatch.resolution.arms import (
    FETCH,
    RRF_K,
    TOP,
    Answer,
    fuse,
    hybrid,
    ordered,
    trigram,
    vector,
)
from docmatch.resolution.catalog import Entry, mint
from docmatch.resolution.conftest import BucketEmbedder
from docmatch.resolution.store import Store


def entries(*descriptions: str) -> list[Entry]:
    return [Entry(mint(each), each) for each in descriptions]


def widgets(count: int) -> list[Entry]:
    """`count` descriptions at the same distance from `widget` on both arms:
    one letter each after it, so every one shares its trigrams and adds two,
    and every one shares its one-hot dimension and adds one."""
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
    assert fetched.over_fetch == "short", "six rows is short of a full fetch"


def test_ordered_reports_a_tie_at_rank_1_and_not_one_lower_down() -> None:
    assert ordered([("SKU-b", 0.2), ("SKU-a", 0.2), ("SKU-c", 0.3)]).tied_at_1
    assert not ordered([("SKU-a", 0.1), ("SKU-b", 0.2), ("SKU-c", 0.2)]).tied_at_1
    assert not ordered([("SKU-a", 0.1)]).tied_at_1


def test_the_over_fetch_check_is_skipped_below_a_full_fetch() -> None:
    assert ordered([("SKU-a", 0.5)] * (FETCH - 1)).over_fetch == "short"
    assert ordered([]).over_fetch == "short"
    assert ordered([]).answers == ()


def test_the_over_fetch_check_fires_when_a_tie_group_reaches_the_boundary() -> None:
    tied = [(f"SKU-{index:02d}", 0.5) for index in range(FETCH)]
    clear = [(f"SKU-{index:02d}", index / 100) for index in range(FETCH)]

    assert ordered(tied).over_fetch == "equal"
    assert ordered(clear).over_fetch == "distinct"


def reciprocal(*ranks: int) -> float:
    """The fused score of an entry at these ranks, one per half it is in,
    summed trigram first as the arm sums them."""
    return sum(1 / (RRF_K + rank) for rank in ranks)


def test_fuse_sums_reciprocal_ranks_over_both_halves_where_they_disagree() -> None:
    """The trigram half ranks a, b, c and the vector half c, d, a: an entry
    in both halves sums both ranks, an entry in one takes its one rank."""
    fetched = fuse(
        [("SKU-a", 0.1), ("SKU-b", 0.2), ("SKU-c", 0.3)],
        [("SKU-c", 0.05), ("SKU-d", 0.06), ("SKU-a", 0.07)],
        depth=25,
    )

    assert fetched.answers == (
        Answer("SKU-a", reciprocal(1, 3)),
        Answer("SKU-c", reciprocal(3, 1)),
        Answer("SKU-b", reciprocal(2)),
        Answer("SKU-d", reciprocal(2)),
    )
    assert RRF_K == 60


def test_fuse_ranks_each_half_by_distance_then_sku_whatever_order_it_came_in() -> None:
    """Two entries tied in a half take their ranks by SKU, as the single arm
    ranks them, so the order the index returned them in moves nothing."""
    trigram_half = [("SKU-b", 0.2), ("SKU-a", 0.2)]
    vector_half = [("SKU-z", 0.1)]

    fetched = fuse(trigram_half, vector_half, depth=25)

    assert fetched == fuse(list(reversed(trigram_half)), vector_half, depth=25)
    assert fetched.answers == (
        Answer("SKU-a", reciprocal(1)),
        Answer("SKU-z", reciprocal(1)),
        Answer("SKU-b", reciprocal(2)),
    )


def test_fuse_breaks_a_tie_in_fused_score_by_sku_at_every_rank_and_at_the_cut() -> None:
    """Three tie groups: a and b swap first and second across the halves and
    sum the same, d and f are each third in one half, c and e each fourth,
    so the cut to five falls inside the last group, which the SKU decides."""
    fetched = fuse(
        [("SKU-b", 0.1), ("SKU-a", 0.2), ("SKU-f", 0.3), ("SKU-e", 0.4)],
        [("SKU-a", 0.1), ("SKU-b", 0.2), ("SKU-d", 0.3), ("SKU-c", 0.4)],
        depth=25,
    )

    assert [each.sku for each in fetched.answers] == [
        "SKU-a",
        "SKU-b",
        "SKU-d",
        "SKU-f",
        "SKU-c",
    ]
    assert fetched.answers[0].score == fetched.answers[1].score
    assert fetched.tied_at_1


def test_fuse_cuts_each_half_to_the_depth_before_it_sums() -> None:
    """At depth 2 the trigram half's third entry is past the cut, so it
    carries only its vector rank."""
    fetched = fuse(
        [("SKU-a", 0.1), ("SKU-b", 0.2), ("SKU-c", 0.3)],
        [("SKU-c", 0.1)],
        depth=2,
    )

    assert fetched.answers == (
        Answer("SKU-a", reciprocal(1)),
        Answer("SKU-c", reciprocal(1)),
        Answer("SKU-b", reciprocal(2)),
    )


def test_fuse_checks_the_over_fetch_on_each_half() -> None:
    """A half fetches `depth + FETCH` rows: a tie group reaching its boundary
    at the depth's cut is reported, and a half short of a full fetch has
    nothing past it. Either half equal makes the arm's check equal."""
    depth = 2
    full = depth + FETCH
    tied = [(f"SKU-{index:02d}", 0.5) for index in range(full)]
    clear = [(f"SKU-{index:02d}", index / 100) for index in range(full)]
    short = [("SKU-a", 0.1)]

    assert fuse(clear, clear, depth).over_fetch == "distinct"
    assert fuse(tied, clear, depth).over_fetch == "equal"
    assert fuse(clear, tied, depth).over_fetch == "equal"
    assert fuse(short, clear, depth).over_fetch == "distinct"
    assert fuse(short, short, depth).over_fetch == "short"
    assert fuse([], [], depth).answers == ()


def test_trigram_returns_five_ordered_by_distance_then_sku(
    store: Store, embedder: BucketEmbedder
) -> None:
    catalog = entries("blue widget", "red widget", "green gadget", "widget")
    catalog += widgets(4)
    store.rebuild(catalog, embedder)

    fetched = trigram(store, "widget")

    assert len(fetched.answers) == TOP
    assert fetched.answers[0].sku == mint("widget")
    assert fetched.answers[0].score == 0.0
    scores = [each.score for each in fetched.answers]
    assert scores == sorted(scores)
    for one, other in zip(fetched.answers, fetched.answers[1:], strict=False):
        assert (one.score, one.sku) < (other.score, other.sku)
    assert not fetched.tied_at_1
    assert fetched.over_fetch == "short"


def test_trigram_breaks_ties_by_sku_whatever_order_the_index_returned(
    store: Store, embedder: BucketEmbedder
) -> None:
    """Eight entries at one distance from the query: the five are the five
    smallest SKUs, whichever order they were inserted in. What order the
    index returns them in is the index's business; `ordered` is pinned on
    every order above, and this pins that the arm applies it."""
    tied = widgets(8)
    expected = sorted(each.sku for each in tied)[:TOP]

    store.rebuild(tied, embedder)
    forwards = trigram(store, "widget")
    store.rebuild(list(reversed(tied)), embedder)
    backwards = trigram(store, "widget")

    assert [each.sku for each in forwards.answers] == expected
    assert forwards == backwards
    assert forwards.tied_at_1
    assert len({each.score for each in forwards.answers}) == 1


def test_trigram_reports_a_tie_group_that_reaches_the_fetched_boundary(
    store: Store, embedder: BucketEmbedder
) -> None:
    """Twenty-six entries at one distance: the index returns 25 of them, the
    boundary's distance equals the cut's, and the run says so rather than
    trusting the index's own order."""
    store.rebuild(widgets(26), embedder)

    fetched = trigram(store, "widget")

    assert fetched.over_fetch == "equal"


def test_trigram_over_fetch_is_enough_when_the_cut_sits_clear_of_the_boundary(
    store: Store, embedder: BucketEmbedder
) -> None:
    close = widgets(4)
    far = entries(*(f"gadget number {index} of many" for index in range(30)))
    store.rebuild(close + far, embedder)

    fetched = trigram(store, "widget")

    assert fetched.over_fetch == "distinct"
    assert {each.sku for each in fetched.answers} > {each.sku for each in close}


def test_trigram_answers_fewer_than_five_over_a_smaller_catalog(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(entries("blue widget", "red widget"), embedder)

    fetched = trigram(store, "widget")

    assert len(fetched.answers) == 2
    assert fetched.over_fetch == "short"


def test_vector_returns_five_ordered_by_distance_then_sku(
    store: Store, embedder: BucketEmbedder
) -> None:
    """The query's own entry at cosine distance 0.0 first, then four of the
    six two-word texts that carry `widget`, all at one shared distance, the
    four smallest SKUs; `green gadget` shares nothing and sits last."""
    two_words = entries("blue widget", "red widget") + widgets(4)
    catalog = [*entries("green gadget", "widget"), *two_words]
    store.rebuild(catalog, embedder)

    fetched = vector(store, embedder, "widget")

    assert len(fetched.answers) == TOP
    assert fetched.answers[0].sku == mint("widget")
    assert fetched.answers[0].score == 0.0
    scores = [each.score for each in fetched.answers]
    assert scores == sorted(scores)
    for one, other in zip(fetched.answers, fetched.answers[1:], strict=False):
        assert (one.score, one.sku) < (other.score, other.sku)
    assert [each.sku for each in fetched.answers[1:]] == sorted(
        each.sku for each in two_words
    )[: TOP - 1]
    assert len({each.score for each in fetched.answers[1:]}) == 1
    assert not fetched.tied_at_1
    assert fetched.over_fetch == "short"


def test_vector_embeds_the_query_once_per_call(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(entries("blue widget"), embedder)
    del embedder.calls[:]

    vector(store, embedder, "widget")
    vector(store, embedder, "widget b")

    assert embedder.calls == [("widget",), ("widget b",)]


def test_vector_breaks_ties_by_sku_whatever_order_the_index_returned(
    store: Store, embedder: BucketEmbedder
) -> None:
    tied = widgets(8)
    expected = sorted(each.sku for each in tied)[:TOP]

    store.rebuild(tied, embedder)
    forwards = vector(store, embedder, "widget")
    store.rebuild(list(reversed(tied)), embedder)
    backwards = vector(store, embedder, "widget")

    assert [each.sku for each in forwards.answers] == expected
    assert forwards == backwards
    assert forwards.tied_at_1
    assert len({each.score for each in forwards.answers}) == 1


def test_vector_reports_a_tie_group_that_reaches_the_fetched_boundary(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(widgets(26), embedder)

    fetched = vector(store, embedder, "widget")

    assert fetched.over_fetch == "equal"


def test_vector_over_fetch_is_enough_when_the_cut_sits_clear_of_the_boundary(
    store: Store,
) -> None:
    """The far entries share `widget` and add two to thirty-one more words
    each, so every one sits at its own distance and the boundary's differs
    from the cut's. The fillers are in the vocabulary so no two share a
    dimension by hash."""
    fillers = [f"w{index}" for index in range(31)]
    embedder = BucketEmbedder("widget", *string.ascii_lowercase, *fillers)
    close = widgets(4)
    far = entries(*("widget " + " ".join(fillers[:count]) for count in range(2, 32)))
    store.rebuild(close + far, embedder)

    fetched = vector(store, embedder, "widget")

    assert fetched.over_fetch == "distinct"
    assert {each.sku for each in fetched.answers} > {each.sku for each in close}


def test_vector_answers_fewer_than_five_over_a_smaller_catalog(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(entries("blue widget", "red widget"), embedder)

    fetched = vector(store, embedder, "widget")

    assert len(fetched.answers) == 2
    assert fetched.over_fetch == "short"


def test_hybrid_fuses_what_the_single_arms_rank_where_they_disagree(
    store: Store, embedder: BucketEmbedder
) -> None:
    """Five entries, so each single arm's five is its whole ranking: the
    trigram arm puts the misspelled entry near the top and the vector arm
    puts the reordered words first, and the hybrid is the fusion of both
    rankings at the distances the single arms measured."""
    catalog = entries("blue widget", "widget blu", "widget", "red widget", "gadget")
    store.rebuild(catalog, embedder)
    by_trigram = trigram(store, "widget blue")
    by_vector = vector(store, embedder, "widget blue")

    fetched = hybrid(store, embedder, "widget blue", depth=25)

    assert [each.sku for each in by_trigram.answers] != [
        each.sku for each in by_vector.answers
    ]
    assert fetched == fuse(
        [(each.sku, each.score) for each in by_trigram.answers],
        [(each.sku, each.score) for each in by_vector.answers],
        depth=25,
    )
    assert len(fetched.answers) == TOP


def test_hybrid_breaks_ties_by_sku_whatever_order_the_index_returned(
    store: Store, embedder: BucketEmbedder
) -> None:
    """Eight entries tied in both halves: each half ranks them by SKU, so the
    fused five are the five smallest SKUs in that order, at five distinct
    fused scores, whichever order they were inserted in."""
    tied = widgets(8)
    expected = sorted(each.sku for each in tied)[:TOP]

    store.rebuild(tied, embedder)
    forwards = hybrid(store, embedder, "widget", depth=25)
    store.rebuild(list(reversed(tied)), embedder)
    backwards = hybrid(store, embedder, "widget", depth=25)

    assert [each.sku for each in forwards.answers] == expected
    assert forwards == backwards
    assert [each.score for each in forwards.answers] == [
        reciprocal(rank, rank) for rank in range(1, TOP + 1)
    ]
    assert forwards.over_fetch == "short"


def test_hybrid_embeds_the_query_once_per_call(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(entries("blue widget"), embedder)
    del embedder.calls[:]

    hybrid(store, embedder, "widget", depth=25)

    assert embedder.calls == [("widget",)]


def test_hybrid_reports_a_tie_group_that_reaches_a_half_boundary(
    store: Store, embedder: BucketEmbedder
) -> None:
    """At depth 1 each half fetches 26 rows; 26 entries at one distance fill
    both, and the boundary's distance equals the cut's."""
    store.rebuild(widgets(26), embedder)

    fetched = hybrid(store, embedder, "widget", depth=1)

    assert fetched.over_fetch == "equal"


def test_hybrid_over_fetches_past_the_search_list_at_a_deep_half(
    store: Store,
) -> None:
    """An HNSW scan returns at most `hnsw.ef_search` rows, pinned at 100, so
    at depth 100 the vector half asks for 125 and would come back short of
    them, with no row past its cut. 130 entries at one vector distance fill
    the half to its boundary, so the tie group reaching it is the arm's; the
    trigram half sits clear at its cut, since the fillers' lengths differ,
    so the outcome is the vector half's or nothing. The planner scans 130
    rows without the index, which the real catalog never is, so the test
    asks for the index the way the real catalog gets it."""
    fillers = [f"f{index}" for index in range(130)]
    embedder = BucketEmbedder("widget", *fillers)
    store.rebuild(entries(*(f"widget {filler}" for filler in fillers)), embedder)
    store.connection.execute("SET enable_seqscan = off")

    fetched = hybrid(store, embedder, "widget", depth=100)

    assert fetched.over_fetch == "equal"
