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

The gate and its ablation
-------------------------

Every pinned document's reading, derived values included, goes through
`gate`, and a reading the gate fails is scored like any other. The report
counts verdicts, and gate pass rate is passed over checked: a reading no rule
could check counts on neither side, so coverage does not hide inside the rate.

The ablation asks what the gate is worth, over checked readings only. A
reading is wrong when any value a checked rule used is not matched against its
label by the field scorer, so the gate is judged on errors it could see and not
on a misread vendor name no date or total could reveal. A catch is a wrong
reading the gate failed, a miss a wrong reading it passed, and a false alarm a
right reading it failed. When the run saved a confidence, the sweep in
`confidence` sets it beside the gate at every fixed edge, and the calibration
table says what each bucket of it is worth; a run with none has no signal.

Counts, not values
------------------

Every breakdown here is counts. `docmatch score` prints the values it matched
and missed, because one document in a terminal is how a human reads what went
wrong; a subset report is pasted into a commit message or a README, and rule 6
of `CLAUDE.md` does not allow document text to leave the machine. Which
fieldtype is failing is what the breakdown is for, and a count says that.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from docmatch.docile.dataset import DocileDataset
from docmatch.evals.confidence import (
    Calibration,
    Checked,
    Judged,
    Sweep,
    bucketed,
    gated_confidence,
    judge_cells,
    judge_header,
    sweep,
)
from docmatch.evals.manifest import Manifest
from docmatch.extraction.derived import DERIVED_FIELDTYPES, derive, with_derived
from docmatch.extraction.extractor import Confidence
from docmatch.gate import RULES, GateResult, RuleName, gate
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
from docmatch.metrics.normalization import normalize
from docmatch.metrics.score import MicroAverage, Score, micro_average, ratio

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


class RunRecord(BaseModel):
    """What a run's `run.json` names it by: the backend and the model asked for.

    The file carries tokens, cost and latency too, which nothing scoring a
    run reads, so they are left to the reader of the file."""

    backend: str
    requested_model: str

    @property
    def name(self) -> str:
        return f"{self.backend} {self.requested_model}"


def read_run_record(path: Path) -> RunRecord:
    """A run's record, or a message saying what is wrong with it."""
    try:
        return RunRecord.model_validate_json(path.read_bytes())
    except OSError as error:
        raise PredictionError(f"cannot read the run record {path}: {error}") from error
    except ValidationError as error:
        raise PredictionError(
            f'{path} is not a run record: expected a JSON object with a "backend" '
            f'and a "requested_model". {first_problem(error)}'
        ) from error


CURRENCY_SYMBOLS = TypeAdapter(dict[str, dict[str, tuple[str, ...]]])
"""A run's currency symbols: document id to amount fieldtype to symbols."""

CurrencySymbolsByDocument = Mapping[str, Mapping[str, Sequence[str]]]


def read_currency_symbols(path: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    """The currency symbols a vendor returned, saved beside a run's predictions.

    A run with no such file has none: a backend that returns no symbols, a run
    saved before the file existed, or a predictions file written by hand.
    """
    try:
        body = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise PredictionError(
            f"cannot read the currency symbols {path}: {error}"
        ) from error
    try:
        return CURRENCY_SYMBOLS.validate_json(body)
    except ValidationError as error:
        raise PredictionError(
            f"{path} is not a run's currency symbols: expected a JSON object keyed "
            "by document id, each mapping an amount fieldtype to a list of "
            f"symbols. {first_problem(error, within=1)}"
        ) from error


CONFIDENCE = TypeAdapter(dict[str, Confidence])
"""A run's confidence: document id to what the backend returned beside the reading."""

ConfidenceByDocument = Mapping[str, Confidence]


def read_confidence(path: Path) -> dict[str, Confidence]:
    """The confidence a backend returned, saved beside a run's predictions.

    A run with no such file has none, and neither does one whose file is empty:
    a backend that returns no confidence writes `{}`, which is "no signal".
    """
    try:
        body = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise PredictionError(f"cannot read the confidence {path}: {error}") from error
    try:
        return CONFIDENCE.validate_json(body)
    except ValidationError as error:
        raise PredictionError(
            f"{path} is not a run's confidence: expected a JSON object keyed by "
            'document id, each holding "fields", a fieldtype to a list of '
            'confidences, and "line_items", a list of cells to one confidence. '
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
    gate: GateResult
    """The gate on the reading plus its derived values."""
    wrong: bool
    """Whether a value a checked rule used is unmatched against its label."""
    values: tuple[Judged, ...]
    """Each distinct header value read, with its confidence and verdict."""
    cells: tuple[Judged, ...]
    """Each predicted cell's values, with its confidence and verdict."""
    gated_confidence: tuple[float | None, ...]
    """The confidence of every value a checked rule used."""


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
class GateTotals:
    """How the gate went across the subset, in counts."""

    passed: int
    failed: int
    not_checked: int
    unreadable: dict[RuleName, int]
    """Readings on which a rule could not read a value, per rule."""

    @property
    def checked(self) -> int:
        return self.passed + self.failed

    @property
    def pass_rate(self) -> float:
        """Passed over checked. Vacuously 1.0 when nothing was checked."""
        return ratio(self.passed, self.checked)


@dataclass(frozen=True)
class Ablation:
    """What the gate caught among checked readings, judged on the values it used."""

    catches: int
    """Wrong, and the gate failed it."""
    misses: int
    """Wrong, and the gate passed it."""
    false_alarms: int
    """Right, and the gate failed it."""


@dataclass(frozen=True)
class SubsetScore:
    """One run of the eval: every pinned document scored, and what was off."""

    manifest: Manifest
    documents: tuple[DocumentScore, ...]
    unpinned: tuple[str, ...]
    """Predicted, and not in the manifest, so not in either number."""
    has_confidence: bool
    """Whether the run saved any confidence, without which there is no signal."""

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
                as_read=as_read.get(fieldtype, _no_totals(fieldtype)),
                with_derived=with_derived.get(fieldtype, _no_totals(fieldtype)),
            )
            for fieldtype in DERIVED_FIELDTYPES
        )

    @property
    def gate(self) -> GateTotals:
        """Gate verdicts over every pinned document, and unreadable values."""
        verdicts = Counter(document.gate.verdict for document in self.documents)
        unreadable: dict[RuleName, int] = {rule: 0 for rule in RULES}
        for document in self.documents:
            for check in document.gate.checks:
                if check.outcome == "unreadable":
                    unreadable[check.rule] += 1
        return GateTotals(
            passed=verdicts["passed"],
            failed=verdicts["failed"],
            not_checked=verdicts["not checked"],
            unreadable=unreadable,
        )

    @property
    def ablation(self) -> Ablation:
        """Catches, misses, and false alarms over checked readings."""
        outcomes = Counter(
            (document.gate.verdict, document.wrong) for document in self.documents
        )
        return Ablation(
            catches=outcomes["failed", True],
            misses=outcomes["passed", True],
            false_alarms=outcomes["failed", False],
        )

    @property
    def calibration(self) -> Calibration | None:
        """Header values and cells by confidence bucket, or None for no signal."""
        if not self.has_confidence:
            return None
        return Calibration(
            header=bucketed(
                each for document in self.documents for each in document.values
            ),
            cells=bucketed(
                each for document in self.documents for each in document.cells
            ),
        )

    @property
    def sweep(self) -> Sweep | None:
        """The gate beside a confidence edge over checked readings, or None."""
        if not self.has_confidence:
            return None
        return sweep(
            [
                Checked(
                    failed=document.gate.verdict == "failed",
                    wrong=document.wrong,
                    confidence=document.gated_confidence,
                )
                for document in self.documents
                if document.gate.verdict != "not checked"
            ]
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


def _no_totals(fieldtype: str) -> FieldTypeTotals:
    """A fieldtype nothing was labeled, read or derived for, counted as zeros."""
    return FieldTypeTotals(fieldtype=fieldtype, matched=0, missing=0, spurious=0)


NOTHING = Prediction()
"""What a document the run left out is scored against."""

NO_CONFIDENCE = Confidence()
"""What a document with no saved confidence is judged with: none on any value."""


def score_subset(
    dataset: DocileDataset,
    manifest: Manifest,
    predictions: Mapping[str, Prediction],
    *,
    currency_symbols: CurrencySymbolsByDocument | None = None,
    confidence: ConfidenceByDocument | None = None,
) -> SubsetScore:
    """Score a run against the documents the manifest pins, in manifest order.

    `currency_symbols` are what a vendor returned beside each document's
    amounts, the second source of its derived currency. `confidence` is what
    it returned beside each reading; a run with none has no signal.
    """
    symbols = currency_symbols or {}
    saved = confidence or {}
    documents = tuple(
        _score_document(
            dataset,
            document_id,
            predictions.get(document_id),
            symbols.get(document_id, {}),
            saved.get(document_id, NO_CONFIDENCE),
        )
        for document_id in manifest.document_ids
    )
    pinned = set(manifest.document_ids)
    return SubsetScore(
        manifest=manifest,
        documents=documents,
        unpinned=tuple(
            document_id for document_id in predictions if document_id not in pinned
        ),
        has_confidence=bool(saved),
    )


def _score_document(
    dataset: DocileDataset,
    document_id: str,
    prediction: Prediction | None,
    currency_symbols: Mapping[str, Sequence[str]],
    confidence: Confidence,
) -> DocumentScore:
    annotation = dataset.annotation(document_id)
    predicted = prediction is not None
    prediction = NOTHING if prediction is None else prediction
    labeled = labeled_fields(annotation)
    reading = with_derived(prediction.header, derive(prediction, currency_symbols))
    fields = score_fields(labeled, reading)
    as_read = score_fields(labeled, prediction.header)
    line_items = score_line_items(labeled_line_items(annotation), prediction.rows)
    gated = gate(reading)
    return DocumentScore(
        document_id=document_id,
        predicted=predicted,
        fields=fields,
        fields_as_read=as_read,
        line_items=line_items,
        gate=gated,
        wrong=_wrong(gated, fields),
        values=judge_header(prediction.fields, confidence, as_read),
        cells=judge_cells(prediction.line_items, confidence, line_items),
        gated_confidence=gated_confidence(prediction.fields, confidence, gated),
    )


def _wrong(gated: GateResult, fields: FieldScore) -> bool:
    """Whether the field scorer left any value the gate used unmatched."""
    matched = {each.fieldtype: set(each.matched) for each in fields.per_fieldtype}
    return any(
        normalize(fieldtype, text) not in matched.get(fieldtype, set())
        for fieldtype, text in gated.used
    )
