"""Tests for running a backend over the fixed subset."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.docile.dataset import DatasetNotFoundError, DocileDataset
from docmatch.evals.manifest import Manifest, load
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
    write_manifest,
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
        if isinstance(given, BaseException):  # a Ctrl-C is one of the answers
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
    wait: Callable[[float], None] = lambda seconds: None,
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
                wait=wait,
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
    retrying from a document this backend cannot read. One attempt at a dollar
    is already past a five cent cap, so the second is refused.
    """
    extractor = FakeExtractor(
        answers=[ExtractionError("half an answer", Decimal("1.00"))]
    )

    run = done(extractor, dataset, pinned, attempts=5, cost_cap=Decimal("0.05"))

    assert [each.attempts for each in run.failed] == [1, 1]
    assert [each.cost for each in run.failed] == [Decimal("1.00"), Decimal("1.00")]
    assert "gave up after spending" in (run.failed[0].failure or "")


def test_adds_up_what_a_document_was_billed_for_attempts_that_failed(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """A billed failure is part of what the document cost, and of the run's cost.

    An answer that came back and did not fit the schema was paid for exactly
    like one that did. Leaving it out would under-report the column the
    benchmark exists to compare backends on.
    """
    extractor = FakeExtractor(
        answers=[ExtractionError("did not fit the schema", Decimal("0.002")), READING],
        cost=Decimal("0.003"),
    )

    run = done(extractor, dataset, pinned)

    first = run.documents[0]
    assert first.predicted
    assert first.attempts == 2
    assert first.cost == Decimal("0.005")


def test_a_call_that_never_reached_the_model_costs_nothing(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    extractor = FakeExtractor(answers=[ExtractionError("the connection went away")])

    run = done(extractor, dataset, pinned, attempts=2)

    assert run.cost == Decimal(0)
    assert [each.attempts for each in run.failed] == [2, 2]


def test_a_missing_pdf_fails_its_own_document_and_not_the_run(
    tmp_path: Path, pinned: Manifest
) -> None:
    """A run that has already paid for eighty-nine documents must keep them.

    `dataset.pdf` raises `DocumentNotFoundError`, which is a `DocileError` and
    not a `PageError`, so this is the case that used to end the whole run and
    write nothing at all.
    """
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    write_pdf(pdfs / "syn0001.pdf")
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, DocileDataset(tmp_path), pinned)

    assert [each.document_id for each in run.predicted] == ["syn0001"]
    assert [each.document_id for each in run.failed] == ["syn0002"]
    assert "syn0002" in (run.failed[0].failure or "")


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


def test_writes_the_subset_the_run_covered(
    dataset: DocileDataset, pinned: Manifest, tmp_path: Path
) -> None:
    """A `--limit` run covers a prefix, and its score has to be over that prefix.

    Scored against the whole pinned subset instead, the documents that were
    never attempted would count as recall the backend lost.
    """
    run = done(FakeExtractor(answers=[READING]), dataset, pinned)
    path = tmp_path / "manifest.json"

    write_manifest(run, path)

    assert load(path).document_ids == ("syn0001", "syn0002")


def test_adds_up_the_tokens_of_the_attempts_that_failed(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """The tokens of a billed failure go where its cost goes.

    Otherwise a retried document records the cost of every attempt with the
    tokens of only the last, and the rates in the record stop reproducing
    the money.
    """
    extractor = FakeExtractor(
        answers=[
            ExtractionError(
                "did not fit the schema",
                Decimal("0.002"),
                usage=Usage(input_tokens=100, output_tokens=50),
            ),
            READING,
        ]
    )

    run = done(extractor, dataset, pinned)

    assert run.documents[0].usage == Usage(input_tokens=200, output_tokens=100)
    assert run.tokens == Usage(input_tokens=300, output_tokens=150)


def test_stops_at_a_failure_the_backend_says_is_not_worth_retrying(
    dataset: DocileDataset, pinned: Manifest
) -> None:
    """A refused key is refused again; the backoff would teach nobody anything."""
    waits: list[float] = []
    extractor = FakeExtractor(
        answers=[ExtractionError("the key was refused", retryable=False)]
    )

    run = done(extractor, dataset, pinned, attempts=3, wait=waits.append)

    assert [each.attempts for each in run.failed] == [1, 1]
    assert waits == []
    assert "refused" in (run.failed[0].failure or "")


def test_a_missing_dataset_ends_the_run_rather_than_every_document(
    tmp_path: Path, pinned: Manifest
) -> None:
    """One wrong --data-dir is one error, not a hundred documents with no PDF."""
    extractor = FakeExtractor(answers=[READING])

    with pytest.raises(DatasetNotFoundError):
        done(extractor, DocileDataset(tmp_path / "never-downloaded"), pinned)
