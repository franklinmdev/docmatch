"""Tests for running a backend over the fixed subset."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.manifest import Manifest
from docmatch.evals.run import read_predictions
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import Extraction, ExtractionError, Usage
from docmatch.extraction.pages import PageImage
from docmatch.extraction.run import (
    ATTEMPTS,
    COST_CAP,
    DocumentRun,
    Run,
    extract_subset,
    percentile_of,
    write_predictions,
    write_record,
)
from docmatch.metrics.fields import Prediction

READING = Prediction(fields={"vendor_name": ["Northwind Trading Ltd"]})


@dataclass
class FakeExtractor:
    """Answers with what it was told to, in order, once per attempt."""

    answers: Sequence[object]
    latency: float = 1.0
    cost: Decimal = Decimal("0.001")
    attempts: list[int] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake"

    def extract(self, pages: Sequence[PageImage]) -> Extraction:
        self.attempts.append(len(pages))
        given = self.answers[min(len(self.attempts), len(self.answers)) - 1]
        if isinstance(given, Exception):
            raise given
        assert isinstance(given, Prediction)
        return Extraction(
            prediction=given,
            usage=Usage(input_tokens=100, output_tokens=50),
            cost=self.cost,
            latency=self.latency,
        )


@pytest.fixture
def dataset(tmp_path: Path) -> DocileDataset:
    """A dataset holding the PDFs of two documents and nothing else."""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    write_pdf(pdfs / "syn0001.pdf")
    write_pdf(pdfs / "syn0002.pdf", pages=2)
    return DocileDataset(tmp_path)


@pytest.fixture
def pinned() -> Manifest:
    return Manifest(split="val", seed=1, size=2, document_ids=("syn0001", "syn0002"))


def done(
    extractor: FakeExtractor,
    dataset: DocileDataset,
    pinned: Manifest,
    *,
    attempts: int = ATTEMPTS,
    cost_cap: Decimal = COST_CAP,
) -> Run:
    """A finished run, with the waiting between attempts taken out."""
    return Run(
        backend=extractor.name,
        manifest=pinned,
        long_edge=1600,
        documents=tuple(
            extract_subset(
                extractor,
                dataset,
                pinned,
                long_edge=1600,
                attempts=attempts,
                cost_cap=cost_cap,
                wait=lambda seconds: None,
            )
        ),
    )


def test_reads_every_pinned_document_in_manifest_order(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, dataset, pinned)

    assert [each.document_id for each in run.documents] == ["syn0001", "syn0002"]
    assert [each.pages for each in run.documents] == [1, 2]
    assert run.predictions() == {"syn0001": READING, "syn0002": READING}


def test_retries_a_document_that_failed_and_keeps_the_reading(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    extractor = FakeExtractor(answers=[ExtractionError("rate limited"), READING])

    run = done(extractor, dataset, pinned)

    first = run.documents[0]
    assert first.attempts == 2
    assert first.predicted
    assert first.failure is None


def test_gives_up_after_its_attempts_and_says_why(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """A document that fails is left out, not written down as empty.

    `evals.run` scores a pinned document with no prediction as a prediction of
    nothing and lists its id, so the recall is lost either way and the two
    reports name the same documents from two directions.
    """
    extractor = FakeExtractor(answers=[ExtractionError("the page is a photograph")])

    run = done(extractor, dataset, pinned, attempts=2)

    assert run.predictions() == {}
    assert [each.attempts for each in run.failed] == [2, 2]
    assert "photograph" in (run.failed[0].failure or "")


def test_stops_retrying_a_document_that_has_spent_its_cap(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """An attempt that reached the model is paid for whether or not it was usable.

    So the cap counts what a document has cost so far rather than how many
    times it has been tried, which is what separates a rate limit worth
    retrying from a document this backend cannot read.
    """
    extractor = FakeExtractor(
        answers=[ExtractionError("half an answer")], cost=Decimal("1.00")
    )

    run = done(extractor, dataset, pinned, attempts=5, cost_cap=Decimal("0.05"))

    assert [each.attempts for each in run.failed] == [5, 5]


def test_reports_a_document_whose_pages_cannot_be_rendered(
    tmp_path: Path, pinned: Manifest
) -> None:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    write_pdf(pdfs / "syn0001.pdf")
    (pdfs / "syn0002.pdf").write_bytes(b"not a PDF at all")
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, DocileDataset(tmp_path), pinned)

    assert [each.document_id for each in run.failed] == ["syn0002"]
    assert run.failed[0].attempts == 0


def test_charges_the_run_over_every_pinned_document(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """A backend that failed half the subset has not earned half off its price."""
    extractor = FakeExtractor(answers=[READING, ExtractionError("no")])

    run = done(extractor, dataset, pinned, attempts=1)

    assert len(run.predicted) == 1
    assert run.cost_per_document == run.cost / 2


def test_adds_up_the_tokens_of_the_whole_run(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, dataset, pinned)

    assert run.tokens.input_tokens == 200
    assert run.tokens.output_tokens == 100


def test_takes_latency_from_the_documents_that_produced_something(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """A document that timed out three times is broken, not slow.

    Counting its three waits as its latency would report the backend as slower
    than it is, when what the run should report is one more failure.
    """
    extractor = FakeExtractor(answers=[READING, ExtractionError("gone")], latency=2.0)

    run = done(extractor, dataset, pinned, attempts=1)

    assert run.latency(50) == 2.0
    assert run.latency(95) == 2.0


def test_the_percentile_is_a_value_some_document_actually_had() -> None:
    values = [float(each) for each in range(1, 101)]

    assert percentile_of(values, 50) == 50.0
    assert percentile_of(values, 95) == 95.0
    assert percentile_of(values, 100) == 100.0
    assert percentile_of([], 50) == 0.0


def test_refuses_a_percentile_that_is_not_one() -> None:
    with pytest.raises(ValueError):
        percentile_of([1.0], 0)


def test_writes_the_predictions_the_eval_reads(
    dataset: DocileDataset, pinned: Manifest, tmp_path: Path
) -> None:
    extractor = FakeExtractor(answers=[READING])
    run = done(extractor, dataset, pinned)
    path = tmp_path / "predictions.json"

    write_predictions(run, path)

    assert read_predictions(path) == {"syn0001": READING, "syn0002": READING}


def test_writes_a_record_of_what_each_document_cost(
    dataset: DocileDataset, pinned: Manifest, tmp_path: Path
) -> None:
    extractor = FakeExtractor(answers=[READING])
    run = done(extractor, dataset, pinned)
    path = tmp_path / "run.json"

    write_record(run, path)

    record = json.loads(path.read_text())
    assert record["backend"] == "fake"
    assert record["long_edge"] == 1600
    assert [each["document_id"] for each in record["documents"]] == [
        "syn0001",
        "syn0002",
    ]
    assert all(each["predicted"] for each in record["documents"])


def test_a_document_run_knows_whether_it_predicted_anything() -> None:
    nothing = DocumentRun(
        document_id="syn0001",
        pages=1,
        attempts=3,
        usage=Usage(input_tokens=0, output_tokens=0),
        cost=Decimal(0),
        latency=0.0,
        prediction=None,
        failure="gave up",
    )

    assert not nothing.predicted
