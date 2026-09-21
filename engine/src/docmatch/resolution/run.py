"""One resolution run: the catalog into Postgres, every query through every
arm, scored against the truth the queries carry.

Scoring is by construction (#119): an arm is correct at top-1 when the first
SKU it returns is the query's, and at top-5 when the query's SKU is among the
five. The headline combines the slices at the exact weight, `w` for the
exact queries and `1 - w` for the noisy variants, pinned in code at #116's
measured share of exact readings. Until #128 adds the noisy slice, the exact
slice is the only one and carries the whole weight, so the formula is in
place from the first run and the number reads as the exact slice's.

Latency is everything a query pays on arrival, measured one query at a time
on one connection, after a discarded warmup pass over the same queries, as
p50 and p95 over the accuracy pass (#120). For the trigram arm that is the
SQL and the ordering; the embedding and the rerank join the count with their
arms, and model load is reported once, never per query.

The run also tallies what the over-fetch check found: on how many full
fetches the score at the fetched boundary equalled the score at the cut, and
how many fetches came back short of `FETCH` rows, where the check does not
apply. The report says both rather than the run failing (#120).
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from docmatch.extraction.run import percentile_of
from docmatch.resolution.arms import TOP, Fetched, trigram
from docmatch.resolution.catalog import (
    Catalog,
    CatalogCounts,
    Query,
    QueryKind,
    exact_queries,
)
from docmatch.resolution.store import (
    SCHEMA,
    Machine,
    ServerVersions,
    Store,
    connect,
    machine,
)

EXACT_WEIGHT = 2 / 3
"""The share of a headline the exact queries carry, inside #116's 0.65 to
0.70 exact band, the same for every arm (#119)."""

WEIGHTS: dict[QueryKind, float] = {"exact": EXACT_WEIGHT}
"""Each slice's weight in the headline; the noisy kinds share `1 - w`."""


@dataclass(frozen=True)
class SliceScore:
    """One slice of queries through one arm: how many, and how many right."""

    kind: QueryKind
    n: int
    top1: int
    top5: int

    @property
    def top1_rate(self) -> float | None:
        return _rate(self.top1, self.n)

    @property
    def top5_rate(self) -> float | None:
        return _rate(self.top5, self.n)


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
    """One arm over the whole query set."""

    name: str
    slices: tuple[SliceScore, ...]
    queries: int
    tied_at_1: int
    """Queries whose first two answers shared a score."""
    p50_ms: float
    p95_ms: float
    over_fetch: OverFetch

    @property
    def top1(self) -> float | None:
        return headline([(each.kind, each.top1_rate) for each in self.slices])

    @property
    def top5(self) -> float | None:
        return headline([(each.kind, each.top5_rate) for each in self.slices])

    @property
    def tie_rate(self) -> float | None:
        return _rate(self.tied_at_1, self.queries)


def headline(rates: Sequence[tuple[QueryKind, float | None]]) -> float | None:
    """The slices combined at their weights, over the slices that have a
    rate; None when none has."""
    present = [(WEIGHTS[kind], rate) for kind, rate in rates if rate is not None]
    if not present:
        return None
    total = sum(weight for weight, _ in present)
    return sum(weight * rate for weight, rate in present) / total


@dataclass(frozen=True)
class SliceCount:
    kind: QueryKind
    queries: int


@dataclass(frozen=True)
class Measured:
    """What the arms answered, and where."""

    arms: tuple[ArmResult, ...]
    versions: ServerVersions
    machine: Machine


@dataclass(frozen=True)
class ResolveResult:
    """One run, as the report renders it."""

    catalog: CatalogCounts
    slices: tuple[SliceCount, ...]
    measured: Measured | None
    """None when the catalog had no entry, so there was nothing to resolve
    and no database was touched."""


Arm = Callable[[str], Fetched]
"""An arm as the run drives it: a query's text in, its five out."""


def resolve(catalog: Catalog, url: str, schema: str = SCHEMA) -> ResolveResult:
    """The catalog rebuilt in the database at `url`, every exact query through
    the trigram arm, scored and timed. The schema is the command's; tests
    build in their own so a run's is left for inspection."""
    queries = exact_queries(catalog)
    slices = (SliceCount("exact", len(queries)),)
    if not queries:
        return ResolveResult(catalog.counts, slices, None)
    with connect(url) as connection:
        store = Store(connection, schema)
        store.rebuild(catalog.entries)
        arms = (measure("trigram", lambda text: trigram(store, text), queries),)
        return ResolveResult(
            catalog.counts, slices, Measured(arms, store.versions(), machine())
        )


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
    kinds = sorted({query.kind for query in queries})
    return ArmResult(
        name=name,
        slices=tuple(
            _score(kind, [each for each in answered if each[0].kind == kind])
            for kind in kinds
        ),
        queries=len(queries),
        tied_at_1=sum(fetched.tied_at_1 for _, fetched in answered),
        p50_ms=percentile_of(latencies, 50),
        p95_ms=percentile_of(latencies, 95),
        over_fetch=OverFetch(
            full=sum(
                fetched.boundary_equals_cut is not None for _, fetched in answered
            ),
            equal=sum(fetched.boundary_equals_cut is True for _, fetched in answered),
            short=sum(fetched.boundary_equals_cut is None for _, fetched in answered),
        ),
    )


def _score(kind: QueryKind, answered: Sequence[tuple[Query, Fetched]]) -> SliceScore:
    top1 = top5 = 0
    for query, fetched in answered:
        skus = [each.sku for each in fetched.answers[:TOP]]
        top1 += bool(skus) and skus[0] == query.sku
        top5 += query.sku in skus
    return SliceScore(kind, len(answered), top1, top5)


def _rate(count: int, n: int) -> float | None:
    return count / n if n else None
