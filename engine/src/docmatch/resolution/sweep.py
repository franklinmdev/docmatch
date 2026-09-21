"""The depth sweep: which depths the hybrid and rerank arms should run at,
measured on the development slice every run, and the keep-or-drop rule on
the reranker those depths feed.

d is the depth per hybrid half, the rows each half ranks before the fusion.
The grid is 25, 50 and 100, and the rule, fixed before the first sweep:
the smallest value whose development top-5 is within one point of the
grid's best, ties to the cheaper (#120). The score is the development
slice's headline top-5, weighted by `w` as the scored slice's is. N is the
pairs the rerank arm sends to the reranker, swept over 10, 25 and 50 at the
constant d by the same rule, six runs in all (#131).

The constant is what the scored slice is measured at, never the swept
value, so a disagreement between them shows in every report rather than
silently moving the table: the pairing-floor pattern, where `docmatch match`
prints each floor beside the value its procedure gives (#77).

The verdict is ADR 0001's, fixed before any arm was measured: the reranker
is kept when its headline top-1 clears the hybrid's by at least `MARGIN`
and its p95 per query is at most `CEILING_MS`, both over the scored slice,
and dropped otherwise. Top-1 alone decides; the separability is printed
beside the verdict and never weighed. Kept means hybrid plus rerank is the
arm Phase 4 resolves with; dropped means the rerank arm and its N sweep are
deleted from `docmatch resolve` after the README row lands.
"""

from collections.abc import Sequence
from dataclasses import dataclass

DEPTHS = (25, 50, 100)
"""The grid d is swept over, per hybrid half (#120)."""

DEPTH = 25
"""The depth per hybrid half the scored slice is measured at: the value the
procedure gave on the real development slice at the first run (#130)."""

RERANK_DEPTHS = (10, 25, 50)
"""The grid N is swept over, pairs per query sent to the reranker, never
below 10 so the five always come from reranked candidates (#120)."""

RERANK_DEPTH = 10
"""The pairs per query the scored slice's rerank arm sends: the value the
procedure gave on the real development slice at the first run (#131)."""

MARGIN = 0.01
"""How far the rerank arm's headline top-1 must clear the hybrid's for the
reranker to be kept, one point (ADR 0001)."""

CEILING_MS = 500.0
"""The rerank arm's p95 per query at or under which the reranker may be
kept, on the named machine (ADR 0001)."""

POINT = 0.01
"""How close to the grid's best a smaller value must come to be chosen, one
point of top-5 (#120)."""

_ROUNDING = 1e-9
"""Room for binary floating point, so a value exactly one point from
another counts as one point, as a reader of the table would count it."""


@dataclass(frozen=True)
class Point:
    """One value of the grid and the development top-5 it gave."""

    value: int
    top5: float | None
    """None when the development slice carried no query."""
    n: int


@dataclass(frozen=True)
class Sweep:
    """One sweep: the constant in code and the grid the procedure ran over."""

    name: str
    constant: int
    points: tuple[Point, ...]

    @property
    def chosen(self) -> int | None:
        """The procedure's value, None when no value was scored."""
        return choose(self.points)


def choose(points: Sequence[Point]) -> int | None:
    """The smallest value whose top-5 is within one point of the best."""
    scored = [each for each in points if each.top5 is not None]
    if not scored:
        return None
    best = max(each.top5 for each in scored if each.top5 is not None)
    return min(
        each.value
        for each in scored
        if each.top5 is not None and best - each.top5 <= POINT + _ROUNDING
    )


@dataclass(frozen=True)
class Verdict:
    """ADR 0001's rule applied to the scored slice's two measurements."""

    rerank_top1: float | None
    hybrid_top1: float | None
    p95_ms: float
    """The rerank arm's, per query."""

    @property
    def gain(self) -> float | None:
        """The rerank arm's headline top-1 less the hybrid's, None when
        either has none."""
        if self.rerank_top1 is None or self.hybrid_top1 is None:
            return None
        return self.rerank_top1 - self.hybrid_top1

    @property
    def clears_margin(self) -> bool | None:
        gain = self.gain
        return None if gain is None else gain >= MARGIN - _ROUNDING

    @property
    def under_ceiling(self) -> bool:
        """At or under the ceiling, so exactly 500 ms passes."""
        return self.p95_ms <= CEILING_MS

    @property
    def kept(self) -> bool | None:
        """Kept when both hold, dropped otherwise, None when there is no
        headline to compare."""
        clears = self.clears_margin
        return None if clears is None else clears and self.under_ceiling
