"""The seam every extraction backend plugs into, and what one call costs.

`Extractor` is the whole interface: one document in, one `Extraction` out.
Phase 1 adds a commercial prebuilt invoice model and a second vision LLM
provider behind this same protocol, and the benchmark compares them by running
the same subset through each. Nothing above this module knows which backend
produced a reading.

The document, not its pages, is what crosses the seam (#34). A vision LLM reads
pages rendered to images and a prebuilt invoice model reads the PDF itself, so
each backend prepares its own input from the verified public copy, and a
rendering choice belongs to the backends that render.

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

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from docmatch.metrics.fields import Prediction

MILLION = Decimal(1_000_000)
THOUSAND = Decimal(1_000)


@dataclass(frozen=True)
class Usage:
    """The units one call was billed for: tokens, or pages for a per-page backend."""

    input_tokens: int = 0
    output_tokens: int = 0
    pages: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            pages=self.pages + other.pages,
        )


NOTHING = Usage()


class ExtractionError(Exception):
    """A backend could not turn this document into a reading.

    `cost` and `usage` are what the attempt was billed anyway. An answer that
    came back and could not be used, because it did not fit the schema or the
    interaction did not complete, was paid for exactly like one that could, and
    a run that did not add it up would under-report the column the benchmark
    exists to compare. A call that never reached the model costs nothing and
    leaves both at zero.

    `retryable` says whether sending the same document again could end differently.
    A rate limit, a dropped connection or an answer that did not fit can; a
    document with no pages, a request the provider refuses as malformed or a key
    it refuses at all cannot, and a run that retried those would wait through
    its backoff to learn nothing.
    """

    def __init__(
        self,
        message: str,
        cost: Decimal = Decimal(0),
        *,
        usage: Usage = NOTHING,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.cost = cost
        self.usage = usage
        self.retryable = retryable


def retryable(error: Exception) -> bool:
    """Whether the same request could succeed next time.

    An HTTP error from a vendor SDK carries its status as `status_code`, which
    google-genai and azure-core both name that way. A rate limit or a server
    error is the provider's moment and not this request's; anything else in
    the 4xx range is a request the provider will refuse again, a bad key, a
    model it does not serve or a body it does not accept. An exception with
    no status never reached an answer, which a retry may fix.
    """
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        return True
    return status in (408, 409, 429) or status >= 500


@dataclass(frozen=True, kw_only=True)
class Price:
    """What a model charges, and when that was last read from the vendor.

    A vision LLM charges per token and a prebuilt invoice model per page, so a
    rate a model does not charge is zero, and cost stays list price times the
    units the vendor reported, whichever units they are (#28).
    """

    input_per_million: Decimal = Decimal(0)
    output_per_million: Decimal = Decimal(0)
    per_thousand_pages: Decimal = Decimal(0)
    read: str
    """The date the rates were read, `YYYY-MM-DD`."""

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
        ) / MILLION + usage.pages * self.per_thousand_pages / THOUSAND


@dataclass(frozen=True)
class Document:
    """One pinned document, as a backend is handed it."""

    document_id: str
    path: Path
    """The public copy, already verified against the digest the manifest pins."""
    pages: int
    """The page count admission matched against DocILE's copy."""


class Confidence(BaseModel):
    """A backend's own confidence in each value it read, shaped like its reading.

    Kept beside the reading and never inside the schema a model fills in (#22).
    `fields` lists one confidence per value of a header fieldtype, in the order
    the reading lists the values; `line_items` holds one row per row of the
    reading, a confidence per cell. None is a value the backend returned with
    no confidence, which is never read as 0.0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: dict[str, tuple[float | None, ...]] = {}
    line_items: tuple[dict[str, float | None], ...] = ()


CurrencySymbols = Mapping[str, tuple[str, ...]]
"""Currency symbols a vendor returned beside the amounts it read.

Keyed by the DocILE fieldtype of the amount they came with, `amount_due` or
`amount_total_gross`, which is the source a derived value is tagged with.
"""


@dataclass(frozen=True)
class Extraction:
    """One backend's reading of one document, and what it took."""

    prediction: Prediction
    usage: Usage
    cost: Decimal
    """US dollars, unrounded."""
    latency: float
    """Seconds spent waiting for the backend, one attempt only."""
    served_model: str | None
    """The served model the vendor reports reading with, or None when it reports none.

    Taken off the answer and never filled in from the requested model: a vendor
    that points a name at a new version is what this is here to show.
    """
    confidence: Confidence | None = None
    """The backend's native confidence, or None for a backend that returns none."""
    currency_symbols: CurrencySymbols = field(default_factory=dict)
    """What a vendor returned as the currency of an amount, apart from its text."""


class Extractor(Protocol):
    """A document in, one reading out. The seam phase 1 hangs every backend on."""

    @property
    def model(self) -> str:
        """The requested model, the one every document of a run is asked for."""

    def extract(self, document: Document) -> Extraction:
        """Read one document. Raises `ExtractionError` when it cannot."""
