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

The table is produced once over the scored slice, queries no knob was set on.
The development slice is where the depth sweep runs, before the table: the
hybrid arm at every d of the grid, scored by its development headline top-5,
and the rule in `sweep` applied to the result. The scored slice is then
measured at the constant d in code, never at the swept value, so a
disagreement between the two is printed rather than silently moving the table
(#130). The sweep is scored and not timed. Latency is everything a query pays
on arrival, measured one query at a time on one connection, after a discarded
warmup pass over the same queries, as p50 and p95 over the scored pass,
answerable and out of catalog alike, since a line pays the same whether it is
in the catalog or not (#120, #132). For the trigram arm that is the SQL and
the ordering; for the vector and hybrid arms the embedding of the query too,
and the warmup pass covers the model with it; for the hybrid the one
statement and the fusion. The rerank joins the count with its arm. Model
load, embedding the catalog and building the HNSW index are each timed once
and reported beside the table, never per query. The models are loaded through
the loader given, after the database has answered with both extensions and
only when there is something to resolve, so a database that does not answer,
or one missing an extension, is reported without loading anything.

The run also tallies what the over-fetch check found: on how many full
fetches the score at the fetched boundary equalled the score at the cut, and
how many fetches came back short of `FETCH` rows, where the check does not
apply. The report says both rather than the run failing (#120).

Separability
------------

The out-of-catalog queries never enter the headline, the tie rate or the
table by kind; they feed one diagnostic per arm, how well its top-1 score
tells a query in the catalog from one that is not (#119). Scores enter as
distances, lower first, as the index arms order them; the hybrid's fused
score is higher first (#130) and enters negated, so beyond a cut means the
same thing on every arm, and a query an arm answered with nothing counts as
farthest either way. The four arms' scores are not commensurable, so each cut
anchors to the arm's own answerable distribution: the smallest score that
keeps `KEPT` of answerable at or under it, and the share of out-of-catalog
queries beyond it. The cut is a reporting device; no threshold is chosen,
which is Phase 4 routing's call. Beside it AUROC, answerable positive, the
chance an out-of-catalog query sits farther than an answerable one with ties
at half, which is rank based and so scale free.

Both populations are weighted the way the headline is: the exact queries
carry `w` of their population and the noisy ones the rest. scipy 1.18's
`mannwhitneyu` takes no weights, so AUROC is the weighted sum of its U
statistic over each pairing of an answerable stratum with an out-of-catalog
one, which is exactly the weighted statistic since every query in a stratum
carries the same weight.
"""

import math
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psycopg
from scipy.stats import mannwhitneyu

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

KEPT = (0.99, 0.95, 0.90)
"""The shares of answerable queries each separability cut keeps (#119)."""


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
class Separability:
    """How well one arm's top-1 score tells answerable from out of catalog."""

    rejected: tuple[float | None, ...]
    """At each cut in `KEPT`, the weighted share of out-of-catalog queries
    beyond it; None when there is none to reject."""
    auroc: float | None


@dataclass(frozen=True)
class ArmResult:
    """One arm over the scored slice."""

    name: str
    kinds: tuple[KindScore, ...]
    """One row per kind, exact and the four noise kinds, in the report's
    order, n 0 for a kind the slice does not carry, so the table keeps its
    five rows on a catalog of any size."""
    queries: int
    """The answerable queries, the n of the headline."""
    tied_at_1: int
    """Queries whose first two answers shared a score."""
    p50_ms: float
    p95_ms: float
    """Both over every scored query, answerable and out of catalog."""
    over_fetch: OverFetch
    separability: Separability

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


TopScores = Sequence[tuple[QueryKind, float]]
"""A population's top-1 scores, each beside its query's kind."""


def separability(answerable: TopScores, out_of_catalog: TopScores) -> Separability:
    """The weighted share of out-of-catalog queries beyond each cut in `KEPT`
    of the answerable distribution, and the weighted AUROC."""
    if not answerable or not out_of_catalog:
        return Separability(tuple(None for _ in KEPT), None)
    answerable_strata = _strata(answerable)
    out_of_catalog_strata = _strata(out_of_catalog)
    ascending = sorted(_weighted(answerable_strata))
    rejected = []
    for keep in KEPT:
        cut = _cut(ascending, keep)
        rejected.append(
            sum(
                weight
                for score, weight in _weighted(out_of_catalog_strata)
                if score > cut
            )
        )
    auroc = 0.0
    for answerable_scores, answerable_weight in answerable_strata:
        for out_of_catalog_scores, out_of_catalog_weight in out_of_catalog_strata:
            farther = mannwhitneyu(out_of_catalog_scores, answerable_scores).statistic
            pairs = len(answerable_scores) * len(out_of_catalog_scores)
            auroc += answerable_weight * out_of_catalog_weight * float(farther) / pairs
    return Separability(tuple(rejected), auroc)


def _cut(ascending: Sequence[tuple[float, float]], keep: float) -> float:
    """The smallest score at or under which `keep` of the weight lies; a
    hair of slack so a sum of weights that should reach it does."""
    total = 0.0
    for score, weight in ascending:
        total += weight
        if total >= keep - 1e-9:
            return score
    return ascending[-1][0]


def _strata(scores: TopScores) -> list[tuple[list[float], float]]:
    """The exact and the noisy scores apart, each with its population's
    weight: `w` and `1 - w` when both are present, all of it otherwise, the
    headline's rule."""
    exact = [score for kind, score in scores if kind == "exact"]
    noisy = [score for kind, score in scores if kind != "exact"]
    if exact and noisy:
        return [(exact, EXACT_WEIGHT), (noisy, 1 - EXACT_WEIGHT)]
    return [(exact or noisy, 1.0)]


def _weighted(strata: Sequence[tuple[list[float], float]]) -> list[tuple[float, float]]:
    """Every score with its own weight, its stratum's shared evenly."""
    return [
        (score, weight / len(scores)) for scores, weight in strata for score in scores
    ]


@dataclass(frozen=True)
class Measured:
    """What the arms answered, with what, and where."""

    arms: tuple[ArmResult, ...]
    depth_sweep: Sweep
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
            out_of_catalog = query_set.scored.out_of_catalog

            def hybrid_at(depth: int) -> Arm:
                return lambda text: hybrid(store, embedder, text, depth)

            depth_sweep = sweep(
                "d", DEPTH, DEPTHS, hybrid_at, query_set.development.queries
            )
            arms = (
                measure(
                    "trigram", lambda text: trigram(store, text), scored, out_of_catalog
                ),
                measure(
                    "vector",
                    lambda text: vector(store, embedder, text),
                    scored,
                    out_of_catalog,
                ),
                measure(
                    "hybrid",
                    hybrid_at(DEPTH),
                    scored,
                    out_of_catalog,
                    higher_first=True,
                ),
            )
            return ResolveResult(
                catalog.counts,
                query_set,
                Measured(
                    arms,
                    depth_sweep,
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


def measure(
    name: str,
    arm: Arm,
    queries: Sequence[Query],
    out_of_catalog: Sequence[Query] = (),
    higher_first: bool = False,
) -> ArmResult:
    """Every query through the arm once discarded and once scored and timed,
    the out-of-catalog ones timed and read for their top-1 score only, which
    enters the separability negated when the arm scores higher first."""
    every = [*queries, *out_of_catalog]
    for query in every:
        arm(query.text)
    latencies: list[float] = []
    fetches: list[Fetched] = []
    for query in every:
        started = time.perf_counter()
        fetches.append(arm(query.text))
        latencies.append((time.perf_counter() - started) * 1000)
    answered = list(zip(queries, fetches[: len(queries)], strict=True))
    out_of_catalog_answered = list(
        zip(out_of_catalog, fetches[len(queries) :], strict=True)
    )
    outcomes = Counter(fetched.over_fetch for _, fetched in answered)
    return ArmResult(
        name=name,
        kinds=_by_kind(answered),
        queries=len(queries),
        tied_at_1=sum(fetched.tied_at_1 for _, fetched in answered),
        p50_ms=percentile_of(latencies, 50),
        p95_ms=percentile_of(latencies, 95),
        over_fetch=OverFetch(
            full=outcomes["distinct"] + outcomes["equal"],
            equal=outcomes["equal"],
            short=outcomes["short"],
        ),
        separability=separability(
            _top1(answered, higher_first),
            _top1(out_of_catalog_answered, higher_first),
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
            Point(value, _top5(arm_at(value), queries), len(queries)) for value in grid
        ),
    )


def _top5(arm: Arm, queries: Sequence[Query]) -> float | None:
    """The headline top-5 of the arm over the queries."""
    return _headline_of(
        _by_kind([(query, arm(query.text)) for query in queries]), "top5"
    )


def _by_kind(answered: Sequence[tuple[Query, Fetched]]) -> tuple[KindScore, ...]:
    """One score per kind, in the report's order, n 0 for a kind not there."""
    return tuple(
        _score(kind, [each for each in answered if each[0].kind == kind])
        for kind in QUERY_KINDS
    )


def _top1(answered: Sequence[tuple[Query, Fetched]], higher_first: bool) -> TopScores:
    """Each query's top-1 score beside its kind as a distance, negated when
    the arm scores higher first, and farthest when unanswered."""
    sign = -1 if higher_first else 1
    return [
        (query.kind, sign * fetched.answers[0].score if fetched.answers else math.inf)
        for query, fetched in answered
    ]


def _score(kind: QueryKind, answered: Sequence[tuple[Query, Fetched]]) -> KindScore:
    top1 = top5 = 0
    for query, fetched in answered:
        skus = [each.sku for each in fetched.answers]
        top1 += bool(skus) and skus[0] == query.sku
        top5 += query.sku in skus
    return KindScore(kind, len(answered), top1, top5)
