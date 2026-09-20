"""The pairing floor procedure: what the floors in `tolerances` should be,
measured on DocILE train.

A floor keeps unrelated lines from pairing: typically an extra invoice line
and the purchase-order line a missing line added from another seed, which
without a floor would pair and hide both findings (#77). So it is measured on
unrelated lines, by a rule fixed in advance: per identity cell, `PAIRS` pairs
of lines that carry the cell, each from two different train documents, drawn
with the pinned `SEED`; the floor is the lowest step on a 0.1 grid that at
most `SHARE` of them clear. A pair clears a step when its similarity is at
or above it, which is when pairing counts it as agreement, and it is graded
the way pairing grades it.

A pair is drawn the way the generator draws a missing line's donor: a
document, then one of its lines, so a document with many lines weighs no more
than one with few. Each cell draws from the documents that carry it, on a
fresh stream from the pinned seed, so how many draws one cell takes does not
move the other's.

Never measured on the fixed subset: its readings are the end-to-end row's
test set, and a floor tuned on them would inflate that row (#77). `docmatch
match` prints each constant beside the value this gives, so a constant that
no longer follows its rule shows in every report.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from docmatch.matching.generator import Seed
from docmatch.matching.matcher import IDENTITY_CELLS, alike
from docmatch.matching.records import CellValues, LineCell, cell_values
from docmatch.matching.tolerances import FLOORS

SPLIT = "train"
"""The split the floors are measured on, never the fixed subset's (#77)."""

SEED = 77
"""The random seed the pairs are drawn with, pinned before the first draw."""

PAIRS = 10_000
"""How many pairs of lines each cell's floor is measured on."""

SHARE = Decimal("0.01")
"""The most of those pairs a floor may let clear it."""

STEPS = tuple(step / 10 for step in range(1, 11))
"""The grid a floor is picked from, 0.1 to 1.0."""


@dataclass(frozen=True)
class Floor:
    """One cell's floor as pairing applies it, and as the procedure measures it."""

    cell: LineCell
    constant: float
    pairs: int
    """How many pairs of lines the procedure drew: none when fewer than two
    documents carry the cell."""
    measured: float | None
    """The lowest step at most `SHARE` of the pairs clear, or None when there
    were no pairs to measure it on, or when more than `SHARE` of them are
    identical and clear even the top of the grid."""


def measure(seeds: Sequence[Seed]) -> tuple[Floor, ...]:
    """Each identity cell's floor, measured on the seeds given."""
    return tuple(_measure(seeds, cell) for cell in IDENTITY_CELLS)


def _measure(seeds: Sequence[Seed], cell: LineCell) -> Floor:
    documents = [carried for each in seeds if (carried := _carried(each, cell))]
    if len(documents) < 2:
        return Floor(cell, FLOORS[cell], 0, None)
    rng = random.Random(SEED)
    scores: list[float] = []
    for _ in range(PAIRS):
        one, other = rng.sample(documents, 2)
        scores.append(alike(rng.choice(one), rng.choice(other)))
    measured = next(
        (
            step
            for step in STEPS
            if sum(score >= step for score in scores) <= SHARE * len(scores)
        ),
        None,
    )
    return Floor(cell, FLOORS[cell], len(scores), measured)


def _carried(seed: Seed, cell: LineCell) -> list[CellValues]:
    """The cell on each of the seed's lines that carries it."""
    return [
        values
        for line in seed.invoice.lines
        if (values := cell_values(line, cell)) is not None
    ]
