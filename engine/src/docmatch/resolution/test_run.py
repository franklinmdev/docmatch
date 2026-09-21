"""Tests for the measurement pass on a fake arm, and one run over a real
Postgres: seam 2 of the Phase 3 spec. Every value is made up."""

import time
from collections.abc import Sequence

import pytest

from docmatch.resolution.arms import FETCH, Answer, Fetched, ordered
from docmatch.resolution.catalog import Entry, build_catalog, mint
from docmatch.resolution.conftest import FAKE_VERSIONS, BucketEmbedder, fake_loader
from docmatch.resolution.models import Loaded, Vector
from docmatch.resolution.queries import Query
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

    assert result.kinds[0] == KindScore("exact", 4, 1, 2)
    assert result.top1 == 0.25
    assert result.top5 == 0.5
    assert result.queries == 4


def test_measure_lists_every_kind_with_n_0_for_one_the_slice_does_not_carry() -> None:
    """The by-kind table has five rows whatever the slice carries, so a small
    catalog prints the same shape as the real one."""
    queries = [Query("one", "SKU-1", "exact"), Query("one x", "SKU-1", "extra words")]

    result = measure("fake", lambda text: answering("SKU-1"), queries)

    assert result.kinds == (
        KindScore("exact", 1, 1, 1),
        KindScore("extra words", 1, 1, 1),
        KindScore("letters substituted", 0, 0, 0),
        KindScore("digits dropped", 0, 0, 0),
        KindScore("punctuation", 0, 0, 0),
    )
    assert result.kinds[2].top1_rate is None


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


class Loader:
    """A loader that counts its calls and hands out the fake."""

    def __init__(self, embedder: BucketEmbedder | None = None) -> None:
        self.calls = 0
        self.embedder = embedder or BucketEmbedder()

    def __call__(self) -> Loaded:
        self.calls += 1
        return fake_loader(self.embedder, load_s=0.5)


def test_resolve_over_an_empty_catalog_touches_no_database_and_loads_nothing() -> None:
    catalog = build_catalog([described("Blue widget"), described("Red widget")])
    loader = Loader()

    result = resolve(catalog, "postgresql://localhost:1/nothing", loader)

    assert result.measured is None
    assert result.queries.scored.queries == ()
    assert result.queries.development.queries == ()
    assert result.catalog.entries == 0
    assert loader.calls == 0


def test_resolve_loads_nothing_when_no_database_answers() -> None:
    catalog = build_catalog([described("Blue widget"), described("Blue widget")])
    loader = Loader()

    with pytest.raises(StoreError, match="no database answers"):
        resolve(catalog, "postgresql://localhost:1/nothing", loader)

    assert loader.calls == 0


def test_resolve_answers_every_exact_query_with_its_own_entry_first_on_both_arms(
    database_url: str,
) -> None:
    catalog = build_catalog(
        [
            described("Blue widget", "Red widget", "Green gadget"),
            described("Blue widget", "Red widget", "Green gadget"),
        ]
    )
    loader = Loader()

    result = resolve(catalog, database_url, loader, schema="resolution_test")

    assert result.measured is not None
    trigram, vector = result.measured.arms
    assert (trigram.name, vector.name) == ("trigram", "vector")
    for arm in (trigram, vector):
        assert arm.kinds[0] == KindScore("exact", 3, 3, 3)
        assert arm.queries == 9, "three exact and six variants, no development entry"
        assert sum(each.n for each in arm.kinds[1:]) == 6
        assert arm.over_fetch == OverFetch(full=0, equal=0, short=9)
    assert result.queries.development.entries == 0
    assert result.measured.versions.pg_trgm
    assert result.measured.models == FAKE_VERSIONS
    assert result.measured.load_s == 0.5
    assert result.measured.build.embedding_s >= 0
    assert result.measured.build.index_s >= 0
    assert result.measured.machine.logical_cpus >= 1
    assert loader.calls == 1


def test_the_vector_arm_pays_for_the_embedding_and_not_for_the_load(
    database_url: str,
) -> None:
    """A query's latency is everything it pays on arrival: the vector arm's
    p50 carries the embedder's time, the trigram arm's does not, and the
    load is reported once as what the loader said."""

    class Slow(BucketEmbedder):
        def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
            time.sleep(0.02)
            return super().embed(texts)

    catalog = build_catalog([described("Blue widget"), described("Blue widget")])

    result = resolve(catalog, database_url, Loader(Slow()), schema="resolution_test")

    assert result.measured is not None
    trigram, vector = result.measured.arms
    assert vector.p50_ms >= 20
    assert trigram.p50_ms < 20
    assert result.measured.load_s == 0.5


def test_a_statement_the_server_refuses_is_reported_not_raised(
    database_url: str,
) -> None:
    """`pg_catalog` cannot be dropped, so a run in it is refused after the
    connection, which is a report and not a traceback."""
    catalog = build_catalog([described("Blue widget"), described("Blue widget")])

    with pytest.raises(StoreError, match="the database refused the run"):
        resolve(catalog, database_url, Loader(), schema="pg_catalog")


def test_an_answer_carries_the_sku_and_the_score_the_arm_gave() -> None:
    assert answering("SKU-1").answers == (Answer("SKU-1", 0.0),)
    assert Entry(mint("x"), "x").sku == mint("x")
