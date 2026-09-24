"""The `replay` backend: answers from a saved extraction run, no vendor call.

`serve --backend replay --run <dir>` reads documents the way a live backend
does, through the `Extractor` seam, but every answer is the one a saved run
already paid for. CI walks the real loop on the synthetic fixture with it, and
a local loop over Phase 1's runs costs nothing (#165).

Which saved document an upload is comes from the PDF itself: its SHA-256
against the digests the saved run's manifest pins, the same digest a public
copy is verified by. A PDF the run does not pin fails extraction, not retried.

What one answer costs
---------------------

A saved run keeps each document's totals, not its attempts: `run.json` lists
how many attempts were made and what they were billed together, and no
attempt apart (checked on the three Phase 1 runs, #168). So replay answers in
one attempt, reporting the whole document's units, list-price cost and served
model, and the loop writes it as one vendor call per document. The cost per
document then equals the source run's to the digit. A document the run failed
on is answered with its saved failure and cost, not worth retrying, so it
fails extraction the way it did then. Latency is replay's own, which is none
worth measuring; the source run's latency never enters a loop run.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from docmatch.evals.manifest import ManifestError
from docmatch.evals.manifest import load as load_manifest
from docmatch.evals.public import digest
from docmatch.evals.run import (
    CONFIDENCE_FILE,
    CURRENCY_SYMBOLS_FILE,
    MANIFEST_FILE,
    PREDICTIONS_FILE,
    RECORD_FILE,
    read_confidence,
    read_currency_symbols,
    read_predictions,
)
from docmatch.extraction.extractor import (
    Confidence,
    Document,
    Extraction,
    ExtractionError,
    Usage,
)
from docmatch.metrics.fields import Prediction, PredictionError, first_problem

BACKEND = "replay"


class SavedDocument(BaseModel):
    """One document's line in a saved `run.json`: what it was billed, in all.

    Older runs leave out the counts their backend never reported, so each
    defaults to none."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    pages_billed: int = 0
    cost: Decimal
    failure: str | None = None
    served_model: str | None = None

    @property
    def usage(self) -> Usage:
        return Usage(
            input_tokens=self.input_tokens,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            output_tokens=self.output_tokens,
            pages=self.pages_billed,
        )


class SavedRecord(BaseModel):
    """A saved `run.json` as replay reads it: whose run, and each document."""

    model_config = ConfigDict(frozen=True)

    backend: str
    requested_model: str
    documents: tuple[SavedDocument, ...]


@dataclass(frozen=True)
class Replay:
    """An `Extractor` answering from one saved run."""

    directory: Path
    record: SavedRecord
    by_digest: dict[str, SavedDocument]
    """The PDF's digest to the saved document's line in the record."""
    predictions: dict[str, Prediction]
    currency_symbols: dict[str, dict[str, tuple[str, ...]]]
    confidence: dict[str, Confidence]

    @property
    def model(self) -> str:
        return self.record.requested_model

    def extract(self, document: Document) -> Extraction:
        saved = self.by_digest.get(digest(document.path.read_bytes()))
        if saved is None:
            raise ExtractionError(
                f"{self.directory} pins no PDF with this digest", retryable=False
            )
        found = saved.document_id
        prediction = self.predictions.get(found)
        if prediction is None:
            raise ExtractionError(
                saved.failure or f"{self.directory} saved no reading of {found}",
                saved.cost,
                usage=saved.usage,
                retryable=False,
            )
        return Extraction(
            prediction=prediction,
            usage=saved.usage,
            cost=saved.cost,
            latency=0.0,
            served_model=saved.served_model,
            confidence=self.confidence.get(found),
            currency_symbols=self.currency_symbols.get(found, {}),
        )


def load(directory: Path) -> Replay:
    """A saved run ready to replay, or a message saying what it lacks.

    Its manifest has to pin a digest for every document, since that is how an
    upload is recognized, and its record has to list every one of them, since
    that is what a vendor call is written from."""
    pinned = load_manifest(directory / MANIFEST_FILE)
    if not pinned.digests:
        raise ManifestError(
            f"{directory / MANIFEST_FILE} pins no digests, so no upload can be "
            "recognized as one of its documents"
        )
    record = _read_record(directory / RECORD_FILE)
    listed = {each.document_id: each for each in record.documents}
    unlisted = sorted(set(pinned.document_ids) - set(listed))
    if unlisted:
        raise PredictionError(
            f"{directory / RECORD_FILE} lists no line for {unlisted[0]}, so what "
            "replaying it costs is unknown"
        )
    return Replay(
        directory=directory,
        record=record,
        by_digest={pinned.digests[each]: listed[each] for each in pinned.document_ids},
        predictions=read_predictions(directory / PREDICTIONS_FILE),
        currency_symbols=read_currency_symbols(directory / CURRENCY_SYMBOLS_FILE),
        confidence=read_confidence(directory / CONFIDENCE_FILE),
    )


def _read_record(path: Path) -> SavedRecord:
    try:
        return SavedRecord.model_validate_json(path.read_bytes())
    except OSError as error:
        raise PredictionError(f"cannot read the run record {path}: {error}") from error
    except ValidationError as error:
        raise PredictionError(
            f'{path} is not a run record replay can read: expected "backend", '
            '"requested_model" and "documents", each with its "document_id" and '
            f'"cost". {first_problem(error)}'
        ) from error
