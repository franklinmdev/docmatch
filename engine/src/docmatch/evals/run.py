"""Scoring one run of predictions over the fixed subset.

A run is a predictions file and the manifest it claims to cover. This module
scores every pinned document with the same two scorers `docmatch score` uses on
one document, then adds the three things that only exist at subset scale: how
the counts add up, which pinned documents the run did not predict, and which
predicted documents the manifest does not pin.

Aggregation is a micro-average, which `metrics.score.MicroAverage` explains.

A document the run left out
---------------------------

It is scored as a prediction of nothing rather than dropped. A benchmark over
97 of the 100 pinned documents is not comparable with one over 100, and the
difference would be invisible in the number, so a backend that produced
nothing for a document loses the recall instead of losing the document. The ids
are reported either way: a run that left out a third of the subset is a broken
run, not a low score, and only the list says which.

Counts, not values
------------------

Every breakdown here is counts. `docmatch score` prints the values it matched
and missed, because one document in a terminal is how a human reads what went
wrong; a subset report is pasted into a commit message or a README, and rule 6
of `CLAUDE.md` does not allow document text to leave the machine. Which
fieldtype is failing is what the breakdown is for, and a count says that.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.manifest import Manifest
from docmatch.extraction.derived import DERIVED_FIELDTYPES, derive, with_derived
from docmatch.metrics.fields import (
    FieldScore,
    Prediction,
    PredictionError,
    first_problem,
    labeled_fields,
    score_fields,
)
from docmatch.metrics.line_items import (
    CellAccuracy,
    LineItemScore,
    labeled_line_items,
    score_line_items,
)
from docmatch.metrics.score import MicroAverage, Score, micro_average

PREDICTIONS = TypeAdapter(dict[str, Prediction])
"""A run's predictions: one document id to one document's prediction.

Built once and reused, as pydantic asks: analyzing a type into a core schema
"comes with some non-trivial overhead" (pydantic 2.13.5, docs read 2026-09-12).
"""


def read_predictions(path: Path) -> dict[str, Prediction]:
    """A run's predictions file, or a message saying what is wrong with it."""
    try:
        return PREDICTIONS.validate_json(path.read_bytes())
    except OSError as error:
        raise PredictionError(f"cannot read the predictions {path}: {error}") from error
    except ValidationError as error:
        raise PredictionError(
            f"{path} is not a run's predictions: expected a JSON object keyed by "
            f'document id, each holding one document\'s "fields" and "line_items". '
            f"{first_problem(error, within=1)}"
        ) from error


@dataclass(frozen=True)
class DocumentScore:
    """One document of the subset, scored on both axes."""

    document_id: str
    predicted: bool
    """Whether the run said anything about this document at all."""
    fields: FieldScore
    """The reading plus its derived values, which is what every number takes."""
    fields_as_read: FieldScore
    """The reading alone, so a report can say what code added to it."""
    line_items: LineItemScore


@dataclass(frozen=True)
class FieldTypeTotals(Score):
    """How one KILE fieldtype went across the whole subset, in counts."""

    fieldtype: str
    matched: int
    missing: int
    spurious: int

    @property
    def true_positives(self) -> int:
        return self.matched

    @property
    def false_negatives(self) -> int:
        return self.missing

    @property
    def false_positives(self) -> int:
        return self.spurious


@dataclass(frozen=True)
class DerivedTotals:
    """One fieldtype code adds values to, as read and with those values."""

    fieldtype: str
    as_read: FieldTypeTotals
    with_derived: FieldTypeTotals


@dataclass(frozen=True)
class SubsetScore:
    """One run of the eval: every pinned document scored, and what was off."""

    manifest: Manifest
    documents: tuple[DocumentScore, ...]
    unpinned: tuple[str, ...]
    """Predicted, and not in the manifest, so not in either number."""

    @property
    def fields(self) -> MicroAverage:
        """The field score over the subset."""
        return micro_average(document.fields for document in self.documents)

    @property
    def line_items(self) -> MicroAverage:
        """The line-item score over the subset."""
        return micro_average(document.line_items for document in self.documents)

    @property
    def not_predicted(self) -> tuple[str, ...]:
        """Pinned, and absent from the predictions file."""
        return tuple(
            document.document_id
            for document in self.documents
            if not document.predicted
        )

    @property
    def per_fieldtype(self) -> tuple[FieldTypeTotals, ...]:
        """Every KILE fieldtype seen on either side, with its three counts."""
        return _totals(document.fields for document in self.documents)

    @property
    def derived(self) -> tuple[DerivedTotals, ...]:
        """Every fieldtype code adds values to, as read and with derived values.

        Listed whether or not anything was added or labeled, so the report
        always shows the line and a zero is a finding rather than an absence.
        """
        as_read = {
            each.fieldtype: each
            for each in _totals(document.fields_as_read for document in self.documents)
        }
        with_derived = {each.fieldtype: each for each in self.per_fieldtype}
        return tuple(
            DerivedTotals(
                fieldtype=fieldtype,
                as_read=as_read.get(fieldtype, _none(fieldtype)),
                with_derived=with_derived.get(fieldtype, _none(fieldtype)),
            )
            for fieldtype in DERIVED_FIELDTYPES
        )

    @property
    def per_cell_fieldtype(self) -> tuple[CellAccuracy, ...]:
        """Per-cell accuracy per LIR fieldtype, summed over every table."""
        correct: Counter[str] = Counter()
        labeled: Counter[str] = Counter()
        spurious: Counter[str] = Counter()
        for document in self.documents:
            for each in document.line_items.per_fieldtype:
                correct[each.fieldtype] += each.correct
                labeled[each.fieldtype] += each.labeled
                spurious[each.fieldtype] += each.spurious
        return tuple(
            CellAccuracy(
                fieldtype=fieldtype,
                correct=correct[fieldtype],
                labeled=labeled[fieldtype],
                spurious=spurious[fieldtype],
            )
            for fieldtype in sorted({*correct, *labeled, *spurious})
        )


def _totals(scores: Iterable[FieldScore]) -> tuple[FieldTypeTotals, ...]:
    """Per-fieldtype counts summed over documents, every fieldtype seen."""
    matched: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    spurious: Counter[str] = Counter()
    for score in scores:
        for each in score.per_fieldtype:
            matched[each.fieldtype] += len(each.matched)
            missing[each.fieldtype] += len(each.missing)
            spurious[each.fieldtype] += len(each.spurious)
    return tuple(
        FieldTypeTotals(
            fieldtype=fieldtype,
            matched=matched[fieldtype],
            missing=missing[fieldtype],
            spurious=spurious[fieldtype],
        )
        for fieldtype in sorted({*matched, *missing, *spurious})
    )


def _none(fieldtype: str) -> FieldTypeTotals:
    return FieldTypeTotals(fieldtype=fieldtype, matched=0, missing=0, spurious=0)


NOTHING = Prediction()
"""What a document the run left out is scored against."""


def score_subset(
    dataset: DocileDataset,
    manifest: Manifest,
    predictions: Mapping[str, Prediction],
) -> SubsetScore:
    """Score a run against the documents the manifest pins, in manifest order."""
    documents = tuple(
        _score_document(dataset, document_id, predictions.get(document_id))
        for document_id in manifest.document_ids
    )
    pinned = set(manifest.document_ids)
    return SubsetScore(
        manifest=manifest,
        documents=documents,
        unpinned=tuple(
            document_id for document_id in predictions if document_id not in pinned
        ),
    )


def _score_document(
    dataset: DocileDataset, document_id: str, prediction: Prediction | None
) -> DocumentScore:
    annotation = dataset.annotation(document_id)
    predicted = prediction is not None
    prediction = NOTHING if prediction is None else prediction
    labeled = labeled_fields(annotation)
    return DocumentScore(
        document_id=document_id,
        predicted=predicted,
        fields=score_fields(
            labeled, with_derived(prediction.header, derive(prediction))
        ),
        fields_as_read=score_fields(labeled, prediction.header),
        line_items=score_line_items(labeled_line_items(annotation), prediction.rows),
    )
