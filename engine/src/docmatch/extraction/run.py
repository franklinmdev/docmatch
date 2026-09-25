"""Running one backend over the fixed subset, and what the run cost.

`docmatch eval` scores a predictions file. This is what produces one: every
document the manifest pins, its public copy verified, read by an `Extractor`,
and written out in the shape the scorer already takes. The two halves are kept apart on
purpose. A run costs money and needs a key; scoring is free and deterministic,
and a benchmark row is reproducible only if the second half can be re-run
against a saved answer without paying for the first again.

A document that fails
---------------------

After its attempts are spent, or its cost cap is reached, the document is left
out of the predictions file and its failure is recorded. `evals.run` already
decides what that means for the number: a pinned document with no prediction is
scored as a prediction of nothing, and its id is listed. So a failure costs
recall rather than quietly shrinking the denominator, and the run report and
the eval report name the same documents from two directions.

Retries, and the cap that bounds them
-------------------------------------

An attempt that raises is retried, with a wait that doubles, up to `attempts`.
Two different things are being defended against: a rate limit or a dropped
connection, which the next attempt fixes, and a document this backend cannot
read, which no number of attempts fixes. A backend says which kind it saw when
it can, and a failure it marks as not worth retrying ends the document there,
without the backoff. For the failures it cannot tell apart, an answer that did
not fit the schema being the usual one, the cost cap separates them in money
rather than in kind. Every attempt that reached the model is paid for whether
or not its answer was usable, so the cap counts what has been spent on this
document so far and refuses the next attempt once that has reached it. What an
attempt will cost is not known until it has been billed, so a document can end
over the cap by at most one attempt; the cap bounds a runaway, it does not
promise a ceiling to the cent.

Public copies, verified before anything is paid for
----------------------------------------------------

A backend reads a document's public copy, never DocILE's (#16), and the copy it
reads has to be the one the manifest pins. Every copy is checked against its
digest before the first document is sent, so a missing or changed file ends
the run once, the way a missing dataset does, instead of failing one document
among others already paid for or quietly scoring a different file.

One document at a time
----------------------

The run is sequential. It is slower than it could be, and it keeps the latency
column honest: p50 and p95 here are what one document takes, not what one
document takes while nineteen others compete with it for the same rate limit.
"""

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from docmatch.docile.dataset import DatasetNotFoundError, DocileDataset, DocileError
from docmatch.evals import manifest, public
from docmatch.evals.manifest import Manifest
from docmatch.extraction.extractor import (
    NOTHING,
    Confidence,
    CurrencySymbols,
    Document,
    Extraction,
    ExtractionError,
    Extractor,
    Usage,
)
from docmatch.metrics.fields import Prediction
from docmatch.metrics.score import percentile_of

ATTEMPTS = 3
"""How many times one document may be sent before it is given up on."""

COST_CAP = Decimal("0.05")
"""US dollars one document may have spent before it is given up on.

Two orders of magnitude above what a cheap model costs for one invoice, which
is where a cap belongs: it is there to stop a runaway, not to trim a bill.
"""

BACKOFF = 2.0
"""Seconds to wait after the first failed attempt, doubling after each."""


@dataclass(frozen=True)
class DocumentRun:
    """What happened to one document: its reading, or why there is none."""

    document_id: str
    pages: int
    attempts: int
    usage: Usage
    """The tokens or pages of every attempt that was billed, the same way `cost`
    adds them up, so the rates in the record reproduce the money."""
    cost: Decimal
    latency: float
    """Seconds of the attempt that produced the prediction, or of all of them
    when none did."""
    prediction: Prediction | None
    failure: str | None
    served_model: str | None
    """The served model the vendor reports reading the prediction with, None when
    the vendor named none or there is no prediction."""
    confidence: Confidence | None = None
    """The confidence the backend returned with the prediction, when it has one."""
    currency_symbols: CurrencySymbols = field(default_factory=dict)
    """The currency symbols the vendor returned with the prediction."""

    @property
    def predicted(self) -> bool:
        return self.prediction is not None


@dataclass(frozen=True)
class Run:
    """One backend over one manifest, document by document."""

    backend: str
    """Which vendor's backend read the run, as `--backend` names it."""
    requested_model: str
    """The requested model, the same for every document; each one's served model
    is its own."""
    manifest: Manifest
    long_edge: int | None
    """What a rendering backend was asked to render at, kept with the record;
    None for a backend that reads the PDF and renders nothing."""
    documents: tuple[DocumentRun, ...]
    commit: str | None = None
    """The commit the run was extracted on, None outside a git checkout, so
    the regression gate can tell whether its row is fresh (#153)."""
    dirty: bool = False
    """Whether this backend's extraction paths differed from that commit when
    the run started, which makes the commit a lie a row cannot be born from."""

    @property
    def predicted(self) -> tuple[DocumentRun, ...]:
        return tuple(each for each in self.documents if each.predicted)

    @property
    def failed(self) -> tuple[DocumentRun, ...]:
        return tuple(each for each in self.documents if not each.predicted)

    @property
    def cost(self) -> Decimal:
        """What the whole run cost, failed attempts included."""
        return sum((each.cost for each in self.documents), Decimal(0))

    @property
    def cost_per_document(self) -> Decimal:
        """The benchmark's cost column: the run's cost over every pinned document.

        Every document, not only the ones that produced a prediction. A backend
        that fails a third of the subset has not earned a third off its price.
        """
        if not self.documents:
            return Decimal(0)
        return self.cost / len(self.documents)

    @property
    def tokens(self) -> Usage:
        return sum((each.usage for each in self.documents), NOTHING)

    def latency(self, percentile: int) -> float:
        """Seconds at a percentile of the documents that produced a prediction.

        Over the predicted documents only, because a document that failed three
        times spent three timeouts and reporting that as its latency would say
        the backend is slow when what it is, is broken. How many failed is its
        own column.
        """
        return percentile_of([each.latency for each in self.predicted], percentile)

    def predictions(self) -> dict[str, Prediction]:
        """What `docmatch eval` scores."""
        return {
            each.document_id: each.prediction
            for each in self.documents
            if each.prediction is not None
        }


def extract_subset(
    extractor: Extractor,
    dataset: DocileDataset,
    manifest: Manifest,
    copies: Path,
    *,
    attempts: int = ATTEMPTS,
    cost_cap: Decimal = COST_CAP,
    wait: Callable[[float], None] = time.sleep,
) -> Iterator[DocumentRun]:
    """Verify every pinned public copy, then read each document, yielding it as done.

    The verification happens here, when this is called, and not when the first
    document is asked for, so a caller can refuse to start before it has made
    anything. Documents are yielded rather than returned so a long run can
    print progress and a failure on document ninety does not lose the
    eighty-nine before it.
    """
    public.verify(manifest, copies)
    return (
        _document(
            extractor,
            dataset,
            document_id,
            copies,
            attempts=attempts,
            cost_cap=cost_cap,
            wait=wait,
        )
        for document_id in manifest.document_ids
    )


def _document(
    extractor: Extractor,
    dataset: DocileDataset,
    document_id: str,
    copies: Path,
    *,
    attempts: int,
    cost_cap: Decimal,
    wait: Callable[[float], None],
) -> DocumentRun:
    """One document of the subset: its annotation's page count, then its reading."""
    try:
        pages = dataset.annotation(document_id).metadata.page_count
    except DatasetNotFoundError:
        # No dataset at all is the run's failure: every document would fail the
        # same way, and a hundred "no annotation" lines would hide one wrong path.
        raise
    except DocileError as error:
        # A pinned document whose annotation is missing is this document's
        # failure and not the run's: letting it out here would end a paid run on
        # document ninety and write none of the eighty-nine.
        return _failed(
            document_id, pages=0, attempts=0, latency=0.0, failure=str(error)
        )
    document = Document(document_id, public.path(copies, document_id), pages)
    return read_document(
        extractor, document, attempts=attempts, cost_cap=cost_cap, wait=wait
    )


@dataclass(frozen=True)
class Attempt:
    """One request sent for one document, as it came back.

    What the loop keeps as a vendor call, one per attempt, the moment the
    attempt returns and whether or not its reading is ever saved.
    """

    number: int
    """1 for the first attempt."""
    started: datetime
    ended: datetime
    outcome: Extraction | ExtractionError


def read_document(
    extractor: Extractor,
    document: Document,
    *,
    attempts: int = ATTEMPTS,
    cost_cap: Decimal = COST_CAP,
    wait: Callable[[float], None] = time.sleep,
    attempted: Callable[[Attempt], None] = lambda _: None,
) -> DocumentRun:
    """One document, retried until it is read, given up on, or too expensive.

    `attempted` hears of every attempt as it returns, before the next is
    sent, so a caller can keep each one even when the document is never read.
    """
    spent = Decimal(0)
    used = NOTHING
    elapsed = 0.0
    made = 0
    last = "no attempt was made"
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            if spent >= cost_cap:
                last = f"{last}; gave up after spending ${spent:.6f} of ${cost_cap}"
                break
            wait(BACKOFF * 2 ** (attempt - 2))
        made = attempt
        started = time.perf_counter()
        started_at = datetime.now(UTC)
        try:
            read = extractor.extract(document)
        except ExtractionError as error:
            elapsed += time.perf_counter() - started
            attempted(Attempt(attempt, started_at, datetime.now(UTC), error))
            spent += error.cost
            used = used + error.usage
            last = str(error)
            if not error.retryable:
                break
            continue
        attempted(Attempt(attempt, started_at, datetime.now(UTC), read))
        spent += read.cost
        used = used + read.usage
        return DocumentRun(
            document_id=document.document_id,
            pages=document.pages,
            attempts=attempt,
            usage=used,
            cost=spent,
            latency=read.latency,
            prediction=read.prediction,
            failure=None,
            served_model=read.served_model,
            confidence=read.confidence,
            currency_symbols=read.currency_symbols,
        )
    return _failed(
        document.document_id,
        pages=document.pages,
        attempts=made,
        latency=elapsed,
        failure=last,
        cost=spent,
        usage=used,
    )


def _failed(
    document_id: str,
    *,
    pages: int,
    attempts: int,
    latency: float,
    failure: str,
    cost: Decimal = Decimal(0),
    usage: Usage = NOTHING,
) -> DocumentRun:
    return DocumentRun(
        document_id=document_id,
        pages=pages,
        attempts=attempts,
        usage=usage,
        cost=cost,
        latency=latency,
        prediction=None,
        failure=failure,
        served_model=None,
    )


def write_predictions(run: Run, path: Path) -> None:
    """The predictions file `docmatch eval --predictions` takes."""
    body = {
        document_id: prediction.model_dump(exclude_defaults=True)
        for document_id, prediction in run.predictions().items()
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", "utf-8")


def write_manifest(run: Run, path: Path) -> None:
    """The subset this run actually covered, beside its predictions.

    The same file `docmatch eval --manifest` takes. It matters for a `--limit`
    run, whose predictions cover a prefix of the pinned subset and would
    otherwise be scored against all 100 documents, reporting the ones that were
    never attempted as recall the backend lost.
    """
    manifest.write(run.manifest, path)


def write_record(run: Run, path: Path) -> None:
    """Per-document tokens, cost, latency and failures, for reading afterwards.

    Beside the predictions and ignored by git like everything under `data/`.
    Document text never reaches it: the only free text here is a failure
    message, which names what went wrong and not what the page said.
    """
    body = {
        "backend": run.backend,
        "requested_model": run.requested_model,
        "split": run.manifest.split,
        "size": run.manifest.size,
        "long_edge": run.long_edge,
        "commit": run.commit,
        "dirty": run.dirty,
        "cost": str(run.cost),
        "cost_per_document": str(run.cost_per_document),
        "input_tokens": run.tokens.input_tokens,
        "cached_input_tokens": run.tokens.cached_input_tokens,
        "cache_write_tokens": run.tokens.cache_write_tokens,
        "output_tokens": run.tokens.output_tokens,
        "pages_billed": run.tokens.pages,
        "latency_p50": run.latency(50),
        "latency_p95": run.latency(95),
        "documents": [
            {
                "document_id": each.document_id,
                "pages": each.pages,
                "attempts": each.attempts,
                "input_tokens": each.usage.input_tokens,
                "cached_input_tokens": each.usage.cached_input_tokens,
                "cache_write_tokens": each.usage.cache_write_tokens,
                "output_tokens": each.usage.output_tokens,
                "pages_billed": each.usage.pages,
                "cost": str(each.cost),
                "latency": each.latency,
                "predicted": each.predicted,
                "failure": each.failure,
                "served_model": each.served_model,
            }
            for each in run.documents
        ],
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", "utf-8")


def write_confidence(run: Run, path: Path) -> None:
    """Each predicted document's confidence, beside the predictions and apart from them.

    Only documents whose backend returned a confidence are listed, so a run on a
    backend with none writes an empty object, which is what "no signal" reads.
    """
    body = {
        each.document_id: each.confidence.model_dump(mode="json")
        for each in run.predicted
        if each.confidence is not None
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", "utf-8")


def write_currency_symbols(run: Run, path: Path) -> None:
    """The currency symbols a vendor returned beside each predicted document.

    Saved so a derived value can be computed again from a saved run, the same
    way it is from the predictions (#24).
    """
    body = {
        each.document_id: {source: list(symbols) for source, symbols in found.items()}
        for each in run.predicted
        if (found := each.currency_symbols)
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", "utf-8")
