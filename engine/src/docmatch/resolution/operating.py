"""A line resolved in the loop, at the operating threshold (#151).

The hybrid arm at the depth in code answers a line's description against the
train catalog, and the loop keeps its top-1 answer: the SKU and the fused
score when the score is at or above the operating threshold, otherwise no
entry with the score, so the reviewer sees how near the nearest entry came.
It only annotates. No routing reason comes from it, and the matcher never
reads the SKU, since the upload's purchase order and receipt come from the
same labels as the invoice and a SKU comparison would add nothing the
description pairing does not already do (#151).

The operating threshold is set by the procedure in `run`, on the development
slice: the hybrid's top-1 score keeping `KEEP` of the answerable queries,
weighted by `w` the way the headline is. The constant is what the loop runs at, and
`docmatch resolve` prints the procedure's value beside it every run, the
pairing-floor pattern, so a disagreement shows rather than moving the loop.
"""

from pydantic import BaseModel, ConfigDict

from docmatch.matching.records import cell_values
from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import normalize_text
from docmatch.resolution.arms import RRF_K, Fetched, hybrid
from docmatch.resolution.models import Embedder
from docmatch.resolution.store import Store
from docmatch.resolution.sweep import DEPTH

KEEP = 0.95
"""The share of answerable development queries the operating threshold
keeps (#151)."""

OPERATING_THRESHOLD = 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
"""The hybrid's top-1 score below which a line has no entry: the procedure's
value on the real development slice at `bf1a413` (#170), 0.032522 over 1,152
queries. A fused score is a sum of reciprocal ranks, so a line carries its
SKU when the top-1 is first in both halves, or first in one and second in the
other; any other top-1 has no entry."""


class Resolved(BaseModel):
    """One line's resolution: its top-1 SKU at or above the operating
    threshold, and the score either way; both None when the hybrid answered
    nothing."""

    model_config = ConfigDict(frozen=True)

    sku: str | None
    score: float | None


def description_of(line: FieldValues) -> str | None:
    """What a line is resolved by: its description after the text
    normalization the catalog's entries went through, the parts of one read
    in pieces joined in reading order, since a query is one description
    (#119); None when the line carries none."""
    values = cell_values(line, "description")
    if values is None:
        return None
    return " ".join(normalize_text(text) for text in values.texts)


def at_operating_threshold(fetched: Fetched) -> Resolved:
    """The hybrid's top-1 answer kept at the operating threshold."""
    if not fetched.answers:
        return Resolved(sku=None, score=None)
    top = fetched.answers[0]
    return Resolved(
        sku=top.sku if top.score >= OPERATING_THRESHOLD else None, score=top.score
    )


def resolve_line(store: Store, embedder: Embedder, description: str) -> Resolved:
    """A line's description through the hybrid at the depth in code, kept at
    the operating threshold."""
    return at_operating_threshold(hybrid(store, embedder, description, DEPTH))
