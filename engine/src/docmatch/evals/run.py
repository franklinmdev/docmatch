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

Label coverage
--------------

A regional set's labels were written for the fieldtypes its manifest covers
and no others (#213, #214). A reading of any other fieldtype is neither right
nor wrong, since nobody labeled it, so it is dropped from the reading before
either scorer sees it, and from what the ablation and the calibration judge;
the gate still checks the whole reading, as it would in the pipeline. A
manifest declaring no coverage, DocILE's, covers every fieldtype and is
scored exactly as before.

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
from typing import Protocol

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
from docmatch.evals.manifest import Manifest, ManifestError
from docmatch.evals.manifest import load as load_manifest
from docmatch.extraction.derived import DERIVED_FIELDTYPES, derived_header
from docmatch.extraction.extractor import Confidence
from docmatch.gate import RULES, GateResult, RuleName, gate
from docmatch.metrics.fields import (
    FieldScore,
    FieldValues,
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

PREDICTIONS_FILE = "predictions.json"
"""What a run read, the file every eval scores."""

MANIFEST_FILE = "manifest.json"
"""The subset the run claims to cover."""

RECORD_FILE = "run.json"
"""What the run recorded of itself: its backend, its model, what it cost."""

CONFIDENCE_FILE = "confidence.json"
"""What the backend returned beside the reading; empty or absent is no signal."""

CURRENCY_SYMBOLS_FILE = "currency_symbols.json"
"""What the vendor returned beside each amount, when it returns any."""

RUN_FILES = (
    PREDICTIONS_FILE,
    MANIFEST_FILE,
    RECORD_FILE,
    CONFIDENCE_FILE,
    CURRENCY_SYMBOLS_FILE,
)
"""Every file `docmatch extract --out` writes into a run's directory."""

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
    """What a run's `run.json` names it by: the backend and the model asked
    for, and the commit it was extracted on.

    The file carries tokens, cost and latency too, which nothing scoring a
    run reads, so they are left to the reader of the file."""

    backend: str
    requested_model: str
    commit: str | None = None
    """The commit the run was extracted on; None for a run saved before runs
    recorded one (#153)."""
    dirty: bool = False
    """Whether the backend's extraction code differed from that commit."""
    region: str | None = None
    """The region of the set the run read, copied from its manifest; None for
    a neutral set and for a run saved before runs recorded one (#222)."""

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
class SavedRun:
    """A directory `docmatch extract --out` wrote, read back and checked."""

    directory: Path
    record: RunRecord
    predictions: Mapping[str, Prediction]
    currency_symbols: CurrencySymbolsByDocument
    """What the vendor returned beside each document's amounts; empty for a
    run that saved none."""


def read_saved_run(directory: Path, pinned: Manifest, pinned_path: Path) -> SavedRun:
    """A saved run read back, or a message rather than a traceback.

    Strict: its manifest has to be the pinned subset itself, so a `--limit`
    run, which covers a prefix of it, never becomes a row (#75).
    """
    covered_path = directory / MANIFEST_FILE
    covered = load_manifest(covered_path)
    if covered != pinned:
        raise ManifestError(
            f"{covered_path} is not the pinned subset {pinned_path}: it covers "
            f"{covered.size} documents of {covered.split} drawn with seed "
            f"{covered.seed}, and only a run over the whole pinned subset is a row"
        )
    return SavedRun(
        directory=directory,
        record=read_run_record(directory / RECORD_FILE),
        predictions=read_predictions(directory / PREDICTIONS_FILE),
        currency_symbols=read_currency_symbols(directory / CURRENCY_SYMBOLS_FILE),
    )


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
        always shows the line and a zero is a finding rather than an absence;
        a fieldtype the set does not cover is not listed.
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
            if self.manifest.coverage is None or fieldtype in self.manifest.coverage
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


class Labels(Protocol):
    """Where a set's labels come from: DocILE's annotations, or a regional set's
    `labels.csv`."""

    def header(self, document_id: str) -> FieldValues:
        """The document's labeled header values, by fieldtype."""
        ...

    def rows(self, document_id: str) -> tuple[FieldValues, ...]:
        """The document's labeled line items."""
        ...


@dataclass(frozen=True)
class _Docile:
    """DocILE's annotations as labels."""

    dataset: DocileDataset

    def header(self, document_id: str) -> FieldValues:
        return labeled_fields(self.dataset.annotation(document_id))

    def rows(self, document_id: str) -> tuple[FieldValues, ...]:
        return labeled_line_items(self.dataset.annotation(document_id))


def score_subset(
    dataset: DocileDataset | Labels,
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
    labels = _Docile(dataset) if isinstance(dataset, DocileDataset) else dataset
    symbols = currency_symbols or {}
    saved = confidence or {}
    documents = tuple(
        _score_document(
            labels,
            document_id,
            predictions.get(document_id),
            symbols.get(document_id, {}),
            saved.get(document_id, NO_CONFIDENCE),
            manifest.coverage,
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
    labels: Labels,
    document_id: str,
    prediction: Prediction | None,
    currency_symbols: Mapping[str, Sequence[str]],
    confidence: Confidence,
    coverage: tuple[str, ...] | None,
) -> DocumentScore:
    labeled = labels.header(document_id)
    labeled_rows = labels.rows(document_id)
    predicted = prediction is not None
    prediction = NOTHING if prediction is None else prediction
    reading = derived_header(prediction, currency_symbols)
    gated = gate(reading)
    covered, confidence = _covered(prediction, confidence, coverage)
    fields = score_fields(labeled, _only(reading, coverage))
    as_read = score_fields(labeled, covered.header)
    line_items = score_line_items(labeled_rows, covered.rows)
    return DocumentScore(
        document_id=document_id,
        predicted=predicted,
        fields=fields,
        fields_as_read=as_read,
        line_items=line_items,
        gate=gated,
        wrong=_wrong(gated, fields, coverage),
        values=judge_header(covered.fields, confidence, as_read),
        cells=judge_cells(covered.line_items, confidence, line_items),
        gated_confidence=gated_confidence(prediction.fields, confidence, gated),
    )


def _only(values: FieldValues, coverage: tuple[str, ...] | None) -> FieldValues:
    """The values of covered fieldtypes; every value when nothing is declared."""
    if coverage is None:
        return values
    return {key: value for key, value in values.items() if key in coverage}


def _covered(
    prediction: Prediction, confidence: Confidence, coverage: tuple[str, ...] | None
) -> tuple[Prediction, Confidence]:
    """The reading and its confidence with every uncovered value dropped.

    A row left with no covered cell is dropped with its confidence, so the
    confidence of each row left still sits at that row's index.
    """
    if coverage is None:
        return prediction, confidence
    kept = [
        (
            {key: value for key, value in row.items() if key in coverage},
            confidence.line_items[index] if index < len(confidence.line_items) else {},
        )
        for index, row in enumerate(prediction.line_items)
    ]
    kept = [(row, cells) for row, cells in kept if row]
    return (
        Prediction(
            fields={
                key: value
                for key, value in prediction.fields.items()
                if key in coverage
            },
            line_items=tuple(row for row, _ in kept),
        ),
        Confidence(
            fields={
                key: value
                for key, value in confidence.fields.items()
                if key in coverage
            },
            line_items=tuple(
                {key: value for key, value in cells.items() if key in coverage}
                for _, cells in kept
            ),
        ),
    )


def _wrong(
    gated: GateResult, fields: FieldScore, coverage: tuple[str, ...] | None
) -> bool:
    """Whether the field scorer left any covered value the gate used unmatched."""
    matched = {each.fieldtype: set(each.matched) for each in fields.per_fieldtype}
    return any(
        normalize(fieldtype, text) not in matched.get(fieldtype, set())
        for fieldtype, text in gated.used
        if coverage is None or fieldtype in coverage
    )
