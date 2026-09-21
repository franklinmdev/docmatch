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
exactly a sequential scan's five distances (#120, probe 4). The other arms
join in #129, #130 and #131.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from psycopg import sql

from docmatch.resolution.store import Store, StoreError

TOP = 5
"""How many entries every arm returns."""

FETCH = 25
"""How many rows every arm asks its index for before the cut, so a tie group
straddling the cut is ordered in code rather than by the index."""


@dataclass(frozen=True)
class Answer:
    """One entry an arm returned, and how far it sat from the query."""

    sku: str
    score: float


@dataclass(frozen=True)
class Fetched:
    """What an arm answered: its five, and whether the over-fetch was enough."""

    answers: tuple[Answer, ...]
    fetched: int
    """How many rows the index returned, at most `FETCH`."""
    boundary: float | None
    """The score of the last row the index returned, or None when it returned
    none."""

    @property
    def tied_at_1(self) -> bool:
        """Whether the first two answers share a score, which the SKU broke."""
        return len(self.answers) > 1 and self.answers[0].score == self.answers[1].score

    @property
    def boundary_equals_cut(self) -> bool | None:
        """Whether the score at the fetched boundary equals the score at the
        cut, in which case a tie group may reach past what the index returned
        and the five are only as pinned as the index's own order. None when
        the fetch came back short of `FETCH`, where nothing lay beyond it."""
        if self.fetched < FETCH or self.boundary is None:
            return None
        return self.boundary == self.answers[TOP - 1].score


def ordered(rows: Sequence[tuple[str, float]]) -> Fetched:
    """The ordering every arm applies: score, then SKU ascending, cut to five."""
    ranked = sorted(rows, key=lambda row: (row[1], row[0]))
    answers = tuple(Answer(sku, score) for sku, score in ranked[:TOP])
    return Fetched(answers, len(rows), ranked[-1][1] if ranked else None)


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


def _row(sku: object, distance: object) -> tuple[str, float]:
    """One row as the index returned it, its types checked at the boundary."""
    if not isinstance(sku, str) or not isinstance(distance, float):
        raise StoreError(f"the index returned {type(sku)} and {type(distance)}")
    return sku, distance
