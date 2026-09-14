"""The seam every extraction backend plugs into, and what one call costs.

`Extractor` is the whole interface: pages in, one `Extraction` out. Phase 1
adds a second provider, a commercial prebuilt invoice model, an OCR-first path
and a local open-weight model behind this same protocol, and the benchmark
compares them by running the same subset through each. Nothing above this
module knows which backend produced a reading.

Every call reports what it cost. A benchmark row carries cost per document and
p50 and p95 latency beside field F1, because a backend that is two points
better and thirty times dearer is not better, and that trade is only visible if
both numbers come from the same run.

Prices are written down, not fetched
------------------------------------

A price lives in a `Price` beside the model it belongs to, with the date it was
read from the vendor's own page. Computing cost from a rate that silently
changed under an old benchmark row would make two rows incomparable without
either of them looking wrong, so the rate is part of the committed record and
changing it is a visible diff. `notes/fact-check.md` holds the reading log and
the README's backend table carries the same dates.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from docmatch.extraction.pages import PageImage
from docmatch.metrics.fields import Prediction

MILLION = Decimal(1_000_000)


class ExtractionError(Exception):
    """A backend could not turn these pages into a reading.

    `cost` is what the attempt was billed anyway. An answer that came back and
    could not be used, because it did not fit the schema or the interaction did
    not complete, was paid for exactly like one that could, and a run that did
    not add it up would under-report the column the benchmark exists to compare.
    A call that never reached the model costs nothing and leaves it at zero.
    """

    def __init__(self, message: str, cost: Decimal = Decimal(0)) -> None:
        super().__init__(message)
        self.cost = cost


@dataclass(frozen=True)
class Usage:
    """The tokens one call was billed for."""

    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Price:
    """What a model charges, and when that was last read from the vendor."""

    input_per_million: Decimal
    output_per_million: Decimal
    read: str
    """The date the two rates were read, `YYYY-MM-DD`."""

    def of(self, usage: Usage) -> Decimal:
        """What this usage costs, in US dollars, unrounded.

        Kept at full precision rather than rounded to cents: a document here
        costs a small fraction of one, and rounding each document before they
        are summed would report a run of a hundred documents as costing
        nothing at all.
        """
        return (
            usage.input_tokens * self.input_per_million
            + usage.output_tokens * self.output_per_million
        ) / MILLION


@dataclass(frozen=True)
class Extraction:
    """One backend's reading of one document, and what it took."""

    prediction: Prediction
    usage: Usage
    cost: Decimal
    """US dollars, unrounded."""
    latency: float
    """Seconds spent waiting for the backend, one attempt only."""


class Extractor(Protocol):
    """Pages in, one reading out. The seam phase 1 hangs every backend on."""

    @property
    def name(self) -> str:
        """How this backend is named in a benchmark row."""

    def extract(self, pages: Sequence[PageImage]) -> Extraction:
        """Read one document. Raises `ExtractionError` when it cannot."""
