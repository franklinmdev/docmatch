"""One resolution run: the catalog into Postgres, every scored query through
every arm, scored against the truth the queries carry.

Scoring is by construction (#119): an arm is correct at top-1 when the first
SKU it returns is the query's, and at top-5 when the query's SKU is among the
five. The headline is `w` times the rate over the exact queries plus `1 - w`
times the rate over the noisy variants, every kind but exact, with `w`
pinned in code at #116's measured share of exact readings, the same for
every arm. Beside it the same queries regroup by kind, exact and the four
noise kinds, top-1 and top-5 per arm with n, a diagnostic that says where an
arm wins and never reaches the README.

The table is produced once over the scored slice, queries no knob was set
on. The development slice is where the depth sweep runs, before the table:
the hybrid arm at every d of the grid, scored by its development headline
top-5, and the rule in `sweep` applied to the result. The scored slice is
then measured at the constant d in code, never at the swept value, so a
disagreement between the two is printed rather than silently moving the
table (#130). The sweep is scored and not timed. Latency is everything a
query pays on arrival, measured one query at a time on one connection,
after a discarded warmup pass over the same queries, as p50 and p95 over
the scored pass (#120). For the trigram arm that is the SQL and the
ordering; for the vector and hybrid arms the embedding of the query too,
and the warmup pass covers the model with it; for the hybrid the one
statement and the fusion. The rerank joins the count with its arm. Model
load, embedding the catalog and building the HNSW index are each timed once
and reported beside the table, never per query. The models are loaded
through the loader given, after the database has answered with both
extensions and only when there is something to resolve, so a database that
does not answer, or one missing an extension, is reported without loading
anything.

The run also tallies what the over-fetch check found: on how many full
fetches the score at the fetched boundary equalled the score at the cut, and
how many fetches came back short of `FETCH` rows, where the check does not
apply. The report says both rather than the run failing (#120).
"""

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psycopg

from docmatch.metrics.score import percentile_of, share_of
from docmatch.resolution.arms import Fetched, hybrid, trigram, vector
from docmatch.resolution.catalog import Catalog, CatalogCounts
from docmatch.resolution.models import ModelLoader, ModelVersions
from docmatch.resolution.queries import (
    QUERY_KINDS,
    Query,
    QueryKind,
    QuerySet,
    build_query_set,
)
from docmatch.resolution.store import (
    SCHEMA,
    Build,
    Machine,
    ServerVersions,
    Store,
    StoreError,
    connect,
    machine,
)
from docmatch.resolution.sweep import DEPTH, DEPTHS, Point, Sweep

EXACT_WEIGHT = 2 / 3
"""The share of a headline the exact queries carry, inside #116's 0.65 to
0.70 exact band, the same for every arm (#119). The noisy variants share
the rest."""


@dataclass(frozen=True)
class KindScore:
    """The queries of one kind through one arm: how many, and how many right."""

    kind: QueryKind
    n: int
    top1: int
    top5: int

    @property
    def top1_rate(self) -> float | None:
        return share_of(self.top1, self.n)

    @property
    def top5_rate(self) -> float | None:
        return share_of(self.top5, self.n)


@dataclass(frozen=True)
class OverFetch:
    """What the over-fetch check found over one arm's queries."""

    full: int
    """Fetches that returned the full `FETCH` rows, where the check applies."""
    equal: int
    """Of those, the ones whose boundary score equalled the cut's."""
    short: int
    """Fetches that came back short, where the check was skipped."""


@dataclass(frozen=True)
class ArmResult:
    """One arm over the scored slice."""

    name: str
    kinds: tuple[KindScore, ...]
    """One row per kind, exact and the four noise kinds, in the report's
    order, n 0 for a kind the slice does not carry, so the table keeps its
    five rows on a catalog of any size."""
    queries: int
    tied_at_1: int
    """Queries whose first two answers shared a score."""
    p50_ms: float
    p95_ms: float
    over_fetch: OverFetch

    @property
    def top1(self) -> float | None:
        return _headline_of(self.kinds, "top1")

    @property
    def top5(self) -> float | None:
        return _headline_of(self.kinds, "top5")

    @property
    def tie_rate(self) -> float | None:
        return share_of(self.tied_at_1, self.queries)


def headline(exact: float | None, noisy: float | None) -> float | None:
    """`w` times the exact rate plus `1 - w` times the noisy rate; the one
    present when the other is not, and None when neither is."""
    if exact is None:
        return noisy
    if noisy is None:
        return exact
    return EXACT_WEIGHT * exact + (1 - EXACT_WEIGHT) * noisy


def _headline_of(kinds: Sequence[KindScore], at: str) -> float | None:
    """The headline at top-1 or top-5 over queries scored by kind."""
    return headline(
        _rate_over([each for each in kinds if each.kind == "exact"], at),
        _rate_over([each for each in kinds if each.kind != "exact"], at),
    )


def _rate_over(kinds: Sequence[KindScore], at: str) -> float | None:
    """The rate over every query of the kinds given, pooled."""
    correct = sum(each.top1 if at == "top1" else each.top5 for each in kinds)
    return share_of(correct, sum(each.n for each in kinds))


@dataclass(frozen=True)
class Measured:
    """What the arms answered, with what, and where."""

    arms: tuple[ArmResult, ...]
    depth: Sweep
    """The depth sweep on the development slice, beside the constant the
    hybrid arm was measured at."""
    versions: ServerVersions
    models: ModelVersions
    load_s: float
    """Loading the models, once, outside any query's latency."""
    build: Build
    machine: Machine


@dataclass(frozen=True)
class ResolveResult:
    """One run, as the report renders it."""

    catalog: CatalogCounts
    queries: QuerySet
    measured: Measured | None
    """None when the scored slice had no query, so there was nothing to
    resolve and no database was touched."""


Arm = Callable[[str], Fetched]
"""An arm as the run drives it: a query's text in, its five out."""


def resolve(
    catalog: Catalog, url: str, load: ModelLoader, schema: str = SCHEMA
) -> ResolveResult:
    """The catalog rebuilt in the database at `url` with the models `load`
    gives, the scored slice through every arm, scored and timed. The schema
    is the command's; tests build in their own so a run's is left for
    inspection."""
    query_set = build_query_set(catalog)
    scored = query_set.scored.queries
    if not scored:
        return ResolveResult(catalog.counts, query_set, None)
    try:
        with connect(url) as connection:
            store = Store(connection, schema)
            # The extensions are checked before the models load, so a
            # cluster missing one is reported without paying for the load.
            store.versions()
            loaded = load()
            embedder = loaded.embedder
            build = store.rebuild(catalog.entries, embedder)

            def hybrid_at(depth: int) -> Arm:
                return lambda text: hybrid(store, embedder, text, depth)

            depth = sweep("d", DEPTH, DEPTHS, hybrid_at, query_set.development.queries)
            arms = (
                measure("trigram", lambda text: trigram(store, text), scored),
                measure("vector", lambda text: vector(store, embedder, text), scored),
                measure("hybrid", hybrid_at(DEPTH), scored),
            )
            return ResolveResult(
                catalog.counts,
                query_set,
                Measured(
                    arms,
                    depth,
                    store.versions(),
                    loaded.versions,
                    loaded.load_s,
                    build,
                    machine(),
                ),
            )
    except psycopg.Error as error:
        # A refusal after the connection, a schema that cannot be dropped or
        # a statement the server rejects, is a report and not a traceback.
        raise StoreError(f"the database refused the run: {error}") from None


def measure(name: str, arm: Arm, queries: Sequence[Query]) -> ArmResult:
    """Every query through the arm once discarded and once scored and timed."""
    for query in queries:
        arm(query.text)
    latencies: list[float] = []
    answered: list[tuple[Query, Fetched]] = []
    for query in queries:
        started = time.perf_counter()
        fetched = arm(query.text)
        latencies.append((time.perf_counter() - started) * 1000)
        answered.append((query, fetched))
    outcomes = Counter(fetched.over_fetch for _, fetched in answered)
    return ArmResult(
        name=name,
        kinds=tuple(
            _score(kind, [each for each in answered if each[0].kind == kind])
            for kind in QUERY_KINDS
        ),
        queries=len(queries),
        tied_at_1=sum(fetched.tied_at_1 for _, fetched in answered),
        p50_ms=percentile_of(latencies, 50),
        p95_ms=percentile_of(latencies, 95),
        over_fetch=OverFetch(
            full=outcomes["distinct"] + outcomes["equal"],
            equal=outcomes["equal"],
            short=outcomes["short"],
        ),
    )


def sweep(
    name: str,
    constant: int,
    grid: Sequence[int],
    arm_at: Callable[[int], Arm],
    queries: Sequence[Query],
) -> Sweep:
    """Every value of the grid through the arm over the development queries
    once, each scored by its headline top-5, untimed."""
    return Sweep(
        name,
        constant,
        tuple(
            Point(value, _answered(arm_at(value), queries), len(queries))
            for value in grid
        ),
    )


def _answered(arm: Arm, queries: Sequence[Query]) -> float | None:
    """The headline top-5 of the arm over the queries."""
    answered = [(query, arm(query.text)) for query in queries]
    return _headline_of(
        [
            _score(kind, [each for each in answered if each[0].kind == kind])
            for kind in QUERY_KINDS
        ],
        "top5",
    )


def _score(kind: QueryKind, answered: Sequence[tuple[Query, Fetched]]) -> KindScore:
    top1 = top5 = 0
    for query, fetched in answered:
        skus = [each.sku for each in fetched.answers]
        top1 += bool(skus) and skus[0] == query.sku
        top5 += query.sku in skus
    return KindScore(kind, len(answered), top1, top5)
