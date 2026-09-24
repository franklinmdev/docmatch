"""Tests for running a backend over the fixed subset."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.docile.dataset import DatasetNotFoundError, DocileDataset
from docmatch.evals import public
from docmatch.evals.conftest import annotate
from docmatch.evals.manifest import Manifest, load
from docmatch.evals.public import DigestError, PublicCopyError
from docmatch.evals.run import read_predictions
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import (
    Confidence,
    Document,
    Extraction,
    ExtractionError,
    Usage,
)
from docmatch.extraction.run import (
    ATTEMPTS,
    COST_CAP,
    Attempt,
    DocumentRun,
    Run,
    extract_subset,
    read_document,
    write_confidence,
    write_currency_symbols,
    write_manifest,
    write_predictions,
    write_record,
)
from docmatch.metrics.fields import Prediction
from docmatch.metrics.score import percentile_of

READING = Prediction(fields={"vendor_name": ["Northwind Trading Ltd"]})


@dataclass
class FakeExtractor:
    """Answers with what it was told to, in order, once per attempt."""

    answers: Sequence[object]
    latency: float = 1.0
    cost: Decimal = Decimal("0.001")
    served_model: str | None = "fake-002"
    usage: Usage = Usage(input_tokens=100, output_tokens=50)
    confidence: Confidence | None = None
    currency_symbols: dict[str, tuple[str, ...]] = field(default_factory=dict)
    attempts: list[Document] = field(default_factory=list)

    @property
    def model(self) -> str:
        return "fake-001"

    def extract(self, document: Document) -> Extraction:
        self.attempts.append(document)
        given = self.answers[min(len(self.attempts), len(self.answers)) - 1]
        if isinstance(given, BaseException):  # a Ctrl-C is one of the answers
            raise given
        assert isinstance(given, Prediction)
        return Extraction(
            prediction=given,
            usage=self.usage,
            cost=self.cost,
            latency=self.latency,
            served_model=self.served_model,
            confidence=self.confidence,
            currency_symbols=self.currency_symbols,
        )


@dataclass(frozen=True)
class Subset:
    """What a run reads: DocILE's annotations, the public copies, and the pin."""

    dataset: DocileDataset
    copies: Path
    pinned: Manifest


def a_subset(root: Path, pages: Sequence[int] = (1, 2)) -> Subset:
    """One document per entry in `pages`, `syn0001` onwards, that many pages each.

    The annotations and the copies agree on the page count, and the manifest
    pins the digest of each copy as written.
    """
    copies = root / "ucsf"
    copies.mkdir(parents=True)
    digests = {}
    for number, count in enumerate(pages, start=1):
        document_id = f"syn{number:04d}"
        annotate(
            root / "docile",
            document_id,
            original_filename=f"u{document_id}",
            page_sizes=[[1700, 2200]] * count,
        )
        copy = write_pdf(public.path(copies, document_id), pages=count)
        digests[document_id] = public.digest(copy.read_bytes())
    pinned = Manifest(
        split="val",
        seed=1,
        source="ucsf",
        size=len(digests),
        document_ids=tuple(digests),
        digests=digests,
    )
    return Subset(DocileDataset(root / "docile"), copies, pinned)


@pytest.fixture
def subset(tmp_path: Path) -> Subset:
    return a_subset(tmp_path)


def done(
    extractor: FakeExtractor,
    subset: Subset,
    *,
    attempts: int = ATTEMPTS,
    cost_cap: Decimal = COST_CAP,
    wait: Callable[[float], None] = lambda seconds: None,
) -> Run:
    """A finished run, with the waiting between attempts taken out."""
    return Run(
        backend="fake",
        requested_model=extractor.model,
        manifest=subset.pinned,
        long_edge=1600,
        documents=tuple(
            extract_subset(
                extractor,
                subset.dataset,
                subset.pinned,
                subset.copies,
                attempts=attempts,
                cost_cap=cost_cap,
                wait=wait,
            )
        ),
    )


def test_reads_every_pinned_document_in_manifest_order(subset: Subset) -> None:
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, subset)

    assert [each.document_id for each in run.documents] == ["syn0001", "syn0002"]
    assert [each.pages for each in run.documents] == [1, 2]
    assert run.predictions() == {"syn0001": READING, "syn0002": READING}


def test_hands_the_backend_the_public_copy_and_its_admitted_page_count(
    subset: Subset,
) -> None:
    """The backend prepares its own input, so it is given the file, not pages."""
    extractor = FakeExtractor(answers=[READING])

    done(extractor, subset)

    assert extractor.attempts == [
        Document("syn0001", subset.copies / "syn0001.pdf", pages=1),
        Document("syn0002", subset.copies / "syn0002.pdf", pages=2),
    ]


def test_a_missing_public_copy_ends_the_run_before_any_request(
    subset: Subset,
) -> None:
    """One missing file is one message, not a document failure paid around it."""
    public.path(subset.copies, "syn0002").unlink()
    extractor = FakeExtractor(answers=[READING])

    with pytest.raises(PublicCopyError) as raised:
        done(extractor, subset)

    assert "syn0002" in str(raised.value)
    assert "docmatch download" in str(raised.value)
    assert extractor.attempts == []


def test_a_changed_public_copy_ends_the_run_before_any_request(
    subset: Subset,
) -> None:
    """A copy that is not the pinned one would quietly move the benchmark."""
    write_pdf(public.path(subset.copies, "syn0002"), pages=3)
    extractor = FakeExtractor(answers=[READING])

    with pytest.raises(DigestError) as raised:
        done(extractor, subset)

    assert "syn0002" in str(raised.value)
    assert extractor.attempts == []


def test_a_manifest_that_pins_no_digests_is_refused_before_any_request(
    subset: Subset,
) -> None:
    """With nothing pinned there is nothing to verify, so nothing is read."""
    unpinned = subset.pinned.model_copy(update={"digests": {}})
    extractor = FakeExtractor(answers=[READING])

    with pytest.raises(PublicCopyError):
        done(extractor, Subset(subset.dataset, subset.copies, unpinned))

    assert extractor.attempts == []


def test_retries_a_document_that_failed_and_keeps_the_reading(
    subset: Subset,
) -> None:
    extractor = FakeExtractor(answers=[ExtractionError("rate limited"), READING])

    run = done(extractor, subset)

    first = run.documents[0]
    assert first.attempts == 2
    assert first.predicted
    assert first.failure is None


def test_gives_up_after_its_attempts_and_says_why(subset: Subset) -> None:
    """A document that fails is left out, not written down as empty.

    `evals.run` scores a pinned document with no prediction as a prediction of
    nothing and lists its id, so the recall is lost either way and the two
    reports name the same documents from two directions.
    """
    extractor = FakeExtractor(answers=[ExtractionError("the page is a photograph")])

    run = done(extractor, subset, attempts=2)

    assert run.predictions() == {}
    assert [each.attempts for each in run.failed] == [2, 2]
    assert "photograph" in (run.failed[0].failure or "")


def test_stops_retrying_a_document_that_has_spent_its_cap(subset: Subset) -> None:
    """An attempt that reached the model is paid for whether or not it was usable.

    So the cap counts what a document has cost so far rather than how many
    times it has been tried, which is what separates a rate limit worth
    retrying from a document this backend cannot read. One attempt at a dollar
    is already past a five cent cap, so the second is refused.
    """
    extractor = FakeExtractor(
        answers=[ExtractionError("half an answer", Decimal("1.00"))]
    )

    run = done(extractor, subset, attempts=5, cost_cap=Decimal("0.05"))

    assert [each.attempts for each in run.failed] == [1, 1]
    assert [each.cost for each in run.failed] == [Decimal("1.00"), Decimal("1.00")]
    assert "gave up after spending" in (run.failed[0].failure or "")


def test_adds_up_what_a_document_was_billed_for_attempts_that_failed(
    subset: Subset,
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

    run = done(extractor, subset)

    first = run.documents[0]
    assert first.predicted
    assert first.attempts == 2
    assert first.cost == Decimal("0.005")


def test_a_call_that_never_reached_the_model_costs_nothing(subset: Subset) -> None:
    extractor = FakeExtractor(answers=[ExtractionError("the connection went away")])

    run = done(extractor, subset, attempts=2)

    assert run.cost == Decimal(0)
    assert [each.attempts for each in run.failed] == [2, 2]


def test_a_missing_annotation_fails_its_own_document_and_not_the_run(
    subset: Subset,
) -> None:
    """A run that has already paid for eighty-nine documents must keep them.

    The admitted page count is read from the annotation, and a pinned document
    whose annotation is gone is that document's failure. Letting
    `DocumentNotFoundError` out would end a paid run and write nothing at all.
    """
    (subset.dataset.root / "annotations" / "syn0002.json").unlink()
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, subset)

    assert [each.document_id for each in run.predicted] == ["syn0001"]
    assert [each.document_id for each in run.failed] == ["syn0002"]
    assert "syn0002" in (run.failed[0].failure or "")
    assert run.failed[0].attempts == 0


def test_charges_the_run_over_every_pinned_document(subset: Subset) -> None:
    """A backend that failed half the subset has not earned half off its price."""
    extractor = FakeExtractor(answers=[READING, ExtractionError("no")])

    run = done(extractor, subset, attempts=1)

    assert len(run.predicted) == 1
    assert run.cost_per_document == run.cost / 2


def test_adds_up_the_tokens_of_the_whole_run(subset: Subset) -> None:
    extractor = FakeExtractor(answers=[READING])

    run = done(extractor, subset)

    assert run.tokens.input_tokens == 200
    assert run.tokens.output_tokens == 100


def test_takes_latency_from_the_documents_that_produced_something(
    subset: Subset,
) -> None:
    """A document that timed out three times is broken, not slow.

    Counting its three waits as its latency would report the backend as slower
    than it is, when what the run should report is one more failure.
    """
    extractor = FakeExtractor(answers=[READING, ExtractionError("gone")], latency=2.0)

    run = done(extractor, subset, attempts=1)

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


def test_writes_the_predictions_the_eval_reads(subset: Subset, tmp_path: Path) -> None:
    run = done(FakeExtractor(answers=[READING]), subset)
    path = tmp_path / "predictions.json"

    write_predictions(run, path)

    assert read_predictions(path) == {"syn0001": READING, "syn0002": READING}


def test_writes_a_record_of_what_each_document_cost(
    subset: Subset, tmp_path: Path
) -> None:
    run = done(FakeExtractor(answers=[READING]), subset)
    path = tmp_path / "run.json"

    write_record(run, path)

    record = json.loads(path.read_text())
    assert record["backend"] == "fake"
    assert record["requested_model"] == "fake-001"
    assert record["long_edge"] == 1600
    assert [each["document_id"] for each in record["documents"]] == [
        "syn0001",
        "syn0002",
    ]
    assert all(each["predicted"] for each in record["documents"])
    assert [each["served_model"] for each in record["documents"]] == [
        "fake-002",
        "fake-002",
    ]


def test_a_document_that_failed_has_no_served_model(subset: Subset) -> None:
    """No answer was used, so no vendor said which model read it."""
    run = done(FakeExtractor(answers=[ExtractionError("no")]), subset)

    assert [each.served_model for each in run.documents] == [None, None]


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
        served_model=None,
    )

    assert not nothing.predicted


def test_writes_the_subset_the_run_covered(subset: Subset, tmp_path: Path) -> None:
    """A `--limit` run covers a prefix, and its score has to be over that prefix.

    Scored against the whole pinned subset instead, the documents that were
    never attempted would count as recall the backend lost.
    """
    run = done(FakeExtractor(answers=[READING]), subset)
    path = tmp_path / "manifest.json"

    write_manifest(run, path)

    assert load(path).document_ids == ("syn0001", "syn0002")


def test_adds_up_the_tokens_of_the_attempts_that_failed(subset: Subset) -> None:
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

    run = done(extractor, subset)

    assert run.documents[0].usage == Usage(input_tokens=200, output_tokens=100)
    assert run.tokens == Usage(input_tokens=300, output_tokens=150)


def test_stops_at_a_failure_the_backend_says_is_not_worth_retrying(
    subset: Subset,
) -> None:
    """A refused key is refused again; the backoff would teach nobody anything."""
    waits: list[float] = []
    extractor = FakeExtractor(
        answers=[ExtractionError("the key was refused", retryable=False)]
    )

    run = done(extractor, subset, attempts=3, wait=waits.append)

    assert [each.attempts for each in run.failed] == [1, 1]
    assert waits == []
    assert "refused" in (run.failed[0].failure or "")


def test_a_missing_dataset_ends_the_run_rather_than_every_document(
    subset: Subset, tmp_path: Path
) -> None:
    """One wrong --data-dir is one error, not a hundred documents with no labels."""
    extractor = FakeExtractor(answers=[READING])
    elsewhere = DocileDataset(tmp_path / "never-downloaded")

    with pytest.raises(DatasetNotFoundError):
        done(extractor, Subset(elsewhere, subset.copies, subset.pinned))

    assert extractor.attempts == []


def test_a_per_page_document_can_end_after_two_billed_attempts(tmp_path: Path) -> None:
    """At a cent a page, two failed reads of three pages are past a five cent cap.

    The cap counts what was spent and never predicts the next attempt (#35), so
    the third attempt is refused rather than the second.
    """
    three_pages = a_subset(tmp_path, pages=(3,))
    extractor = FakeExtractor(
        answers=[
            ExtractionError(
                "accepted, then polling timed out",
                Decimal("0.03"),
                usage=Usage(pages=3),
            )
        ]
    )

    run = done(extractor, three_pages)

    failed = run.failed[0]
    assert failed.attempts == 2
    assert failed.cost == Decimal("0.06")
    assert failed.usage.pages == 6
    assert "gave up after spending" in (failed.failure or "")


def test_a_page_gap_fails_the_document_at_once_with_what_was_processed(
    tmp_path: Path,
) -> None:
    three_pages = a_subset(tmp_path, pages=(3,))
    extractor = FakeExtractor(
        answers=[
            ExtractionError(
                "2 of 3 pages processed",
                Decimal("0.02"),
                usage=Usage(pages=2),
                retryable=False,
            )
        ]
    )

    run = done(extractor, three_pages)

    failed = run.failed[0]
    assert failed.attempts == 1
    assert failed.cost == Decimal("0.02")
    assert failed.usage.pages == 2


def test_records_the_pages_billed_per_document_and_for_the_run(
    subset: Subset, tmp_path: Path
) -> None:
    extractor = FakeExtractor(
        answers=[READING], usage=Usage(pages=2), cost=Decimal("0.02")
    )
    path = tmp_path / "run.json"

    write_record(done(extractor, subset), path)

    record = json.loads(path.read_text())
    assert record["pages_billed"] == 4
    assert [each["pages_billed"] for each in record["documents"]] == [2, 2]


def test_records_cached_and_cache_write_tokens_per_document_and_for_the_run(
    subset: Subset, tmp_path: Path
) -> None:
    """Cached input and cache writes have their own rates, so their own counts."""
    extractor = FakeExtractor(
        answers=[READING],
        usage=Usage(
            input_tokens=100,
            cached_input_tokens=40,
            cache_write_tokens=30,
            output_tokens=50,
        ),
    )
    path = tmp_path / "run.json"

    write_record(done(extractor, subset), path)

    record = json.loads(path.read_text())
    assert record["cached_input_tokens"] == 80
    assert [each["cached_input_tokens"] for each in record["documents"]] == [40, 40]
    assert record["cache_write_tokens"] == 60
    assert [each["cache_write_tokens"] for each in record["documents"]] == [30, 30]


CONFIDENT = Confidence(
    fields={"vendor_name": (0.93,)},
    line_items=({"line_item_description": 0.71, "line_item_quantity": None},),
)


def test_keeps_confidence_and_currency_symbols_beside_the_reading(
    subset: Subset,
) -> None:
    extractor = FakeExtractor(
        answers=[READING],
        confidence=CONFIDENT,
        currency_symbols={"amount_due": ("$",)},
    )

    run = done(extractor, subset)

    assert run.predictions() == {"syn0001": READING, "syn0002": READING}
    assert [each.confidence for each in run.documents] == [CONFIDENT, CONFIDENT]
    assert [each.currency_symbols for each in run.documents] == [
        {"amount_due": ("$",)},
        {"amount_due": ("$",)},
    ]


def test_writes_confidence_and_currency_symbols_in_their_own_files(
    subset: Subset, tmp_path: Path
) -> None:
    """The predictions file stays exactly what the backend read."""
    run = done(
        FakeExtractor(
            answers=[READING, ExtractionError("no")],
            confidence=CONFIDENT,
            currency_symbols={"amount_total_gross": ("US$",)},
        ),
        subset,
        attempts=1,
    )

    write_confidence(run, tmp_path / "confidence.json")
    write_currency_symbols(run, tmp_path / "currency_symbols.json")

    assert json.loads((tmp_path / "confidence.json").read_text()) == {
        "syn0001": {
            "fields": {"vendor_name": [0.93]},
            "line_items": [{"line_item_description": 0.71, "line_item_quantity": None}],
        }
    }
    assert json.loads((tmp_path / "currency_symbols.json").read_text()) == {
        "syn0001": {"amount_total_gross": ["US$"]}
    }


def test_a_backend_with_no_confidence_writes_empty_files(
    subset: Subset, tmp_path: Path
) -> None:
    run = done(FakeExtractor(answers=[READING]), subset)

    write_confidence(run, tmp_path / "confidence.json")
    write_currency_symbols(run, tmp_path / "currency_symbols.json")

    assert json.loads((tmp_path / "confidence.json").read_text()) == {}
    assert json.loads((tmp_path / "currency_symbols.json").read_text()) == {}


def test_hears_of_every_attempt_as_it_returns(tmp_path: Path) -> None:
    """Each attempt reaches the caller, the failed one with its error and
    what it was billed, so the loop can keep both as vendor calls."""
    failure = ExtractionError("answer did not fit", Decimal("0.002"))
    extractor = FakeExtractor([failure, READING])
    heard: list[Attempt] = []

    read_document(
        extractor,
        Document("doc", write_pdf(tmp_path / "doc.pdf"), 1),
        wait=lambda _: None,
        attempted=heard.append,
    )

    assert [each.number for each in heard] == [1, 2]
    assert heard[0].outcome is failure
    assert isinstance(heard[1].outcome, Extraction)
    assert all(each.started <= each.ended for each in heard)
