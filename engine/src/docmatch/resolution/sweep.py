"""The depth sweep: which depth the hybrid arm should run at, measured on the
development slice every run.

d is the depth per hybrid half, the rows each half ranks before the fusion.
The grid is 25, 50 and 100, and the rule, fixed before the first sweep:
the smallest value whose development top-5 is within one point of the
grid's best, ties to the cheaper (#120). The score is the development
slice's headline top-5, weighted by `w` as the scored slice's is.

The constant is what the scored slice is measured at, never the swept
value, so a disagreement between them shows in every report rather than
silently moving the table: the pairing-floor pattern, where `docmatch match`
prints each floor beside the value its procedure gives (#77).
"""

from collections.abc import Sequence
from dataclasses import dataclass

DEPTHS = (25, 50, 100)
"""The grid d is swept over, per hybrid half (#120)."""

DEPTH = 25
"""The depth per hybrid half the scored slice is measured at: the value the
procedure gave on the real development slice at the first run (#130)."""

POINT = 0.01
"""How close to the grid's best a smaller value must come to be chosen, one
point of top-5 (#120)."""

_ROUNDING = 1e-9
"""Room for binary floating point, so a value exactly one point below the
best counts as within it, as a reader of the table would count it."""


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
