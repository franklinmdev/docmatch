"""The arms: one indexed query each, plus one ordering applied in code.

An arm returns exactly five entries, so top-5 means the same thing in every
row (#120). Its index returns rows with their distances, and the ordering is
applied here rather than in the `ORDER BY`: score, then SKU ascending, at
every rank and at the cut. Putting the SKU in the `ORDER BY` makes the
planner abandon the index, so every arm over-fetches `FETCH` rows before its
cut and the run checks the margin was enough. Without it, 15 percent of
queries came back with a different set of five SKUs at identical distances.

Ties are real: trigram distance ties at rank 1 for 7.8 percent of queries and
somewhere in the top 5 for 47 percent, since digit-drop noise meets entries
that differ only in their digits. Breaking them by SKU is pinned and
pseudorandom, the SKU being a hash of the description, so an arm that scores
in integers is neither punished nor favoured (#119).

Trigram only: pg_trgm `gist_trgm_ops`, `description <-> query` ascending,
`LIMIT 25`, signature length at the default, since the GiST top-5 measured
exactly a sequential scan's five distances (#120, probe 4).

Vector only: the query embedded through the embedder, pgvector HNSW with
`vector_cosine_ops`, `embedding <=> query` ascending, `LIMIT 25`, the
connection's `hnsw.ef_search` at 100 since the store set it (#120, probe 2).
The embedding is part of what the query pays on arrival, so it sits inside
the arm call the run times; the model load does not, and is reported once.

Hybrid: one statement with two CTEs, each the single arm's indexed query at
`LIMIT d + FETCH`, the query embedded once for it (#113, #120). The
statement returns both halves' rows with their distances, and the fusion is
applied here: each half ordered by (distance, SKU) and cut to d, so the
ranks inside a half are the single arm's ranks, then every SKU in either
half, the full outer join, scored `1/(RRF_K + rank)` summed over the halves
it is in, trigram first, and the fused list ordered by score descending
then SKU and cut to five. The join and the sum sit in code rather than in
the statement because the cut to d does: a rank taken before the (distance,
SKU) ordering would be the index's order, not the arm's. Both indexes stay
in use inside the CTEs, checked with `EXPLAIN` on the real catalog, and
the HNSW search list is widened to `d + FETCH` for the statement when that
is past its pin, since the index returns at most that many rows. Each half
runs the over-fetch check at its own cut, d, and a tie group reaching
either half's boundary is the arm's. The statement also returns each row's
canonical description, which the hybrid does not read and the rerank arm
sends to the reranker, so both arms run the one statement.

Rerank: the hybrid's statement and fusion, then the fused list's first N
candidates sent to the reranker as (query, canonical description) pairs in
one call, their new order by reranker score descending then SKU replacing
the first N of the fused order, and the first five the arm's answer (#120,
#131). N is capped at the fused list's length, so a shorter list is
reranked whole, and the grid never goes below 10, so the five always come
from reranked candidates. The over-fetch check is the hybrid's, since the
candidates are.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from psycopg import sql

from docmatch.resolution.models import Embedder, Reranker
from docmatch.resolution.store import Store, StoreError, vector_literal

TOP = 5
"""How many entries every arm returns."""

FETCH = 25
"""How many rows every arm asks its index for before the cut, so a tie group
straddling the cut is ordered in code rather than by the index."""


RRF_K = 60
"""The reciprocal rank fusion constant, `1/(RRF_K + rank)`: 60 as
Cormack, Clarke and Buettcher set it and as #113 carried it into the
statement."""


@dataclass(frozen=True)
class Answer:
    """One entry an arm returned, and its score: for an index arm the
    distance the index measured, lower first; for the hybrid its fused
    score, higher first."""

    sku: str
    score: float


OverFetchOutcome = Literal["distinct", "equal", "short"]
"""What the over-fetch check found for one query: the boundary score distinct
from the cut's, equal to it, or a fetch short of its full rows where the
check does not apply."""


_WORST_FIRST: tuple[OverFetchOutcome, ...] = ("equal", "distinct", "short")
"""How two halves' checks combine into the arm's: a tie group at either
half's boundary is the arm's, and the check applies when either half was a
full fetch (#130)."""


@dataclass(frozen=True)
class Fetched:
    """What an arm answered: its five, and whether the over-fetch was enough."""

    answers: tuple[Answer, ...]
    over_fetch: OverFetchOutcome
    """Whether the score at the fetched boundary equals the score at the
    cut, in which case a tie group may reach past what the index returned
    and the five are only as pinned as the index's own order. Short when
    the fetch came back under its full rows, where nothing lay beyond it."""

    @property
    def tied_at_1(self) -> bool:
        """Whether the first two answers share a score, which the SKU broke."""
        return len(self.answers) > 1 and self.answers[0].score == self.answers[1].score


Row = tuple[str, float]
"""One row as an index returned it: a SKU and its distance."""


def ordered(rows: Sequence[Row]) -> Fetched:
    """The ordering every index arm applies: distance, then SKU ascending,
    cut to five."""
    ranked = _ranked(rows)
    answers = tuple(Answer(sku, score) for sku, score in ranked[:TOP])
    return Fetched(answers, _check(ranked, TOP, FETCH))


def fuse(
    trigram_half: Sequence[Row], vector_half: Sequence[Row], depth: int
) -> Fetched:
    """The hybrid's five: the fused list cut to five."""
    listed, check = fused_list(trigram_half, vector_half, depth)
    return Fetched(listed[:TOP], check)


def fused_list(
    trigram_half: Sequence[Row], vector_half: Sequence[Row], depth: int
) -> tuple[tuple[Answer, ...], OverFetchOutcome]:
    """The whole fused list from the two halves as the statement returned
    them, each fetched at `depth + FETCH` rows: each half ranked and cut to
    `depth`, the reciprocal ranks summed per SKU over both, then fused score
    descending, then SKU ascending; with both halves' over-fetch check."""
    halves = (_ranked(trigram_half), _ranked(vector_half))
    scores: dict[str, float] = {}
    for half in halves:
        for rank, (sku, _) in enumerate(half[:depth], start=1):
            scores[sku] = scores.get(sku, 0.0) + 1 / (RRF_K + rank)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    checks = [_check(half, depth, depth + FETCH) for half in halves]
    return (
        tuple(Answer(sku, score) for sku, score in ranked),
        min(checks, key=_WORST_FIRST.index),
    )


def reorder(
    query: str,
    listed: Sequence[Answer],
    descriptions: Mapping[str, str],
    reranker: Reranker,
    reranked: int,
) -> tuple[Answer, ...]:
    """The fused list with its first `reranked` candidates scored by the
    reranker in one call and ordered by that score descending, then SKU;
    every candidate past them keeps its place and its fused score."""
    head = listed[:reranked]
    if not head:
        return ()
    scores = reranker.score([(query, descriptions[each.sku]) for each in head])
    rescored = sorted(
        (Answer(each.sku, score) for each, score in zip(head, scores, strict=True)),
        key=lambda answer: (-answer.score, answer.sku),
    )
    return (*rescored, *listed[reranked:])


def _ranked(rows: Sequence[Row]) -> list[Row]:
    """Distance, then SKU ascending: the single arms' ranks."""
    return sorted(rows, key=lambda row: (row[1], row[0]))


def _check(ranked: Sequence[Row], cut: int, fetch: int) -> OverFetchOutcome:
    """The over-fetch check on rows fetched `fetch` at a time and cut at
    `cut`: whether the distance at the fetched boundary equals the one at
    the cut."""
    if len(ranked) < fetch:
        return "short"
    return "equal" if ranked[-1][1] == ranked[cut - 1][1] else "distinct"


def trigram(store: Store, query: str) -> Fetched:
    """The trigram arm's five for a query."""
    table = sql.Identifier(store.schema, "catalog")
    rows = store.connection.execute(
        sql.SQL(
            "SELECT sku, description <-> %s FROM {} "
            "ORDER BY description <-> %s LIMIT %s"
        ).format(table),
        (query, query, FETCH),
    ).fetchall()
    return ordered([_row(sku, distance) for sku, distance in rows])


def vector(store: Store, embedder: Embedder, query: str) -> Fetched:
    """The vector arm's five for a query: embedded, then searched by cosine."""
    (embedded,) = embedder.embed([query])
    literal = vector_literal(embedded)
    table = sql.Identifier(store.schema, "catalog")
    rows = store.connection.execute(
        sql.SQL(
            "SELECT sku, embedding <=> %s::vector FROM {} "
            "ORDER BY embedding <=> %s::vector LIMIT %s"
        ).format(table),
        (literal, literal, FETCH),
    ).fetchall()
    return ordered([_row(sku, distance) for sku, distance in rows])


def hybrid(store: Store, embedder: Embedder, query: str, depth: int) -> Fetched:
    """The hybrid arm's five for a query at depth `depth` per half: embedded
    once, both halves fetched in one statement, fused in code."""
    trigram_half, vector_half, _ = _halves(store, embedder, query, depth)
    return fuse(trigram_half, vector_half, depth)


def rerank(
    store: Store,
    embedder: Embedder,
    reranker: Reranker,
    query: str,
    depth: int,
    reranked: int,
) -> Fetched:
    """The rerank arm's five for a query: the hybrid's fused list at depth
    `depth`, its first `reranked` candidates reordered by the reranker."""
    trigram_half, vector_half, descriptions = _halves(store, embedder, query, depth)
    listed, check = fused_list(trigram_half, vector_half, depth)
    reordered = reorder(query, listed, descriptions, reranker, reranked)
    return Fetched(reordered[:TOP], check)


def _halves(
    store: Store, embedder: Embedder, query: str, depth: int
) -> tuple[list[Row], list[Row], dict[str, str]]:
    """The hybrid's one statement: the query embedded once, both halves
    fetched at `depth + FETCH` rows, and every returned SKU's description."""
    (embedded,) = embedder.embed([query])
    literal = vector_literal(embedded)
    table = sql.Identifier(store.schema, "catalog")
    fetch = depth + FETCH
    with store.search_list(fetch):
        rows = store.connection.execute(
            sql.SQL(
                "WITH trigram AS ("
                "SELECT sku, description, description <-> %(query)s AS distance "
                "FROM {table} "
                "ORDER BY description <-> %(query)s LIMIT %(fetch)s), "
                "vector AS ("
                "SELECT sku, description, "
                "embedding <=> %(embedded)s::vector AS distance "
                "FROM {table} "
                "ORDER BY embedding <=> %(embedded)s::vector LIMIT %(fetch)s) "
                "SELECT 'trigram', sku, distance, description FROM trigram "
                "UNION ALL SELECT 'vector', sku, distance, description FROM vector"
            ).format(table=table),
            {"query": query, "embedded": literal, "fetch": fetch},
        ).fetchall()
    halves: dict[str, list[Row]] = {"trigram": [], "vector": []}
    descriptions: dict[str, str] = {}
    for half, sku, distance, description in rows:
        row = _row(sku, distance)
        halves[str(half)].append(row)
        descriptions[row[0]] = str(description)
    return halves["trigram"], halves["vector"], descriptions


def _row(sku: object, distance: object) -> tuple[str, float]:
    """One row as the index returned it, its types checked at the boundary."""
    if not isinstance(sku, str) or not isinstance(distance, float):
        raise StoreError(f"the index returned {type(sku)} and {type(distance)}")
    return sku, distance
