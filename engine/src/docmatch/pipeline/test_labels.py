"""Tests for the labels backend, on the synthetic fixture's pinned copies."""

from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.manifest import ManifestError
from docmatch.evals.manifest import load as load_manifest
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import NOTHING, Document, ExtractionError
from docmatch.metrics.fields import labeled_fields
from docmatch.metrics.line_items import labeled_line_items
from docmatch.pipeline import labels
from docmatch.pipeline.conftest import SYNTHETIC

LOOP_RUN = SYNTHETIC / "runs" / "loop"
COPIES = SYNTHETIC / "copies"


@pytest.fixture
def labeled() -> labels.Labels:
    return labels.load(
        load_manifest(LOOP_RUN / "manifest.json"), DocileDataset(SYNTHETIC)
    )


@pytest.mark.parametrize("document_id", ["eval0003", "eval0004", "eval0005"])
def test_the_reading_is_the_documents_labels(
    labeled: labels.Labels, document_id: str
) -> None:
    """Found by the PDF alone: the document id the loop hands it is its own."""
    read = labeled.extract(Document("17", COPIES / f"{document_id}.pdf", 1))

    annotation = DocileDataset(SYNTHETIC).annotation(document_id)
    assert read.prediction.header == labeled_fields(annotation)
    assert read.prediction.rows == labeled_line_items(annotation)


def test_a_reading_costs_nothing(labeled: labels.Labels) -> None:
    read = labeled.extract(Document("17", COPIES / "eval0005.pdf", 1))

    assert (read.cost, read.usage, read.served_model) == (Decimal(0), NOTHING, None)
    assert (read.confidence, read.currency_symbols) == (None, {})


def test_a_pdf_the_manifest_does_not_pin_fails_extraction(
    labeled: labels.Labels, tmp_path: Path
) -> None:
    unknown = write_pdf(tmp_path / "unknown.pdf", width=500)

    with pytest.raises(ExtractionError, match="pins no PDF") as raised:
        labeled.extract(Document("17", unknown, 1))

    assert not raised.value.retryable
    assert raised.value.cost == Decimal(0)


def test_a_manifest_that_pins_no_digests_is_refused() -> None:
    pinned = load_manifest(SYNTHETIC / "manifest.json")
    assert not pinned.digests

    with pytest.raises(ManifestError, match="pins no digests"):
        labels.load(pinned, DocileDataset(SYNTHETIC))
