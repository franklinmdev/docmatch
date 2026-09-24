"""The `labels` backend: answers with the document's DocILE labels, no vendor.

`serve --backend labels` reads documents through the `Extractor` seam like
any backend, but every reading is the labels themselves, so the labels
control is a loop run like the others and its latency is the loop without
extraction (#152, #172).

Which document an upload is comes from the PDF itself: its SHA-256 against
the digests the manifest pins, as replay recognizes one. A PDF the manifest
does not pin fails extraction, not retried.

It sends no request anywhere, so it costs $0 and the loop writes no vendor
call for it: a vendor call is a request to a vendor, and there is none.
"""

from dataclasses import dataclass
from decimal import Decimal

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.manifest import Manifest, ManifestError
from docmatch.evals.public import digest
from docmatch.extraction.extractor import (
    NOTHING,
    Document,
    Extraction,
    ExtractionError,
)
from docmatch.metrics.fields import FieldValues, Prediction, labeled_fields
from docmatch.metrics.line_items import labeled_line_items

BACKEND = "labels"

MODEL = "DocILE labels"
"""What a labels run records as its requested model: no model reads."""


@dataclass(frozen=True)
class Labels:
    """An `Extractor` answering with the labels of the documents a manifest
    pins."""

    by_digest: dict[str, Prediction]
    """The PDF's digest to its document's labels, as a reading."""

    @property
    def model(self) -> str:
        return MODEL

    def extract(self, document: Document) -> Extraction:
        prediction = self.by_digest.get(digest(document.path.read_bytes()))
        if prediction is None:
            raise ExtractionError(
                "the manifest pins no PDF with this digest", retryable=False
            )
        return Extraction(
            prediction=prediction,
            usage=NOTHING,
            cost=Decimal(0),
            latency=0.0,
            served_model=None,
        )


def load(pinned: Manifest, dataset: DocileDataset) -> Labels:
    """Every pinned document's labels, keyed by its PDF's digest, or a message
    saying the manifest recognizes no upload."""
    if not pinned.digests:
        raise ManifestError(
            "the manifest pins no digests, so no upload can be recognized as "
            "one of its documents"
        )
    return Labels(
        by_digest={
            pinned.digests[each]: _reading(dataset, each)
            for each in pinned.document_ids
        }
    )


def _reading(dataset: DocileDataset, document_id: str) -> Prediction:
    annotation = dataset.annotation(document_id)
    return Prediction(
        fields=_written(labeled_fields(annotation)),
        line_items=tuple(_written(row) for row in labeled_line_items(annotation)),
    )


def _written(values: FieldValues) -> dict[str, str | list[str] | None]:
    return {fieldtype: list(texts) for fieldtype, texts in values.items()}
