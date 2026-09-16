"""What a backend's own confidence is worth: calibration and the sweep.

Only a backend that returns a confidence natively has one (#22), and it is
saved beside the reading, never inside it. This module judges each value that
carries one against the verdict the scorers already gave, then counts.

Calibration
-----------

Two units, header values and line-item cells, each in ten fixed equal-width
buckets from [0.0, 0.1) to [0.9, 1.0]. Fixed rather than quantile buckets, so
two runs put the same confidence in the same bucket. A value returned with no
confidence is counted on its own line, never read as 0.0, and an empty bucket
is shown with n = 0, so nothing silently drops out of the table.

A header value is one distinct normalized value, the unit field F1 counts, and
it is correct when the field scorer matched it. When two readings of a
fieldtype normalize to one value, the higher confidence is kept: the backend
did read that value that confidently. A cell is correct when the row it belongs
to was paired and the paired row carries the same normalized value, which is
the pairing line-item F1 uses; row confidence is not read. Derived values carry
no confidence and never enter the table.

It speaks to precision only. A value the backend missed carries no confidence,
so no bucket can say anything about recall.

The sweep
---------

Beside the gate ablation, over the same checked readings: at each fixed edge
from 0.1 to 0.9, a reading is flagged by confidence when the lowest confidence
over the values its checked rules used is below the edge. A gated value with no
confidence never flags, and the readings holding one are counted, so a missing
confidence is never treated as a low one.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from docmatch.extraction.extractor import Confidence
from docmatch.gate import GateResult
from docmatch.metrics.fields import FieldScore, WrittenValues
from docmatch.metrics.line_items import LineItemScore
from docmatch.metrics.normalization import normalize

EDGES = tuple(tenth / 10 for tenth in range(1, 10))
"""The inner bucket edges, which are also the sweep's edges, 0.1 through 0.9."""


@dataclass(frozen=True)
class Judged:
    """One value a backend read, its confidence, and the scorer's verdict on it."""

    confidence: float | None
    correct: bool


@dataclass(frozen=True)
class Bucket:
    """Values whose confidence falls in [low, high), and how many were right.

    The last bucket is closed at 1.0. The no-confidence line has no bounds.
    """

    low: float | None
    high: float | None
    n: int
    correct: int


@dataclass(frozen=True)
class Buckets:
    """One unit's calibration: ten fixed buckets and the no-confidence line."""

    buckets: tuple[Bucket, ...]
    no_confidence: Bucket


@dataclass(frozen=True)
class Calibration:
    """Header values and line-item cells, bucketed apart."""

    header: Buckets
    cells: Buckets


def bucketed(judged: Iterable[Judged]) -> Buckets:
    """Every judged value in its fixed bucket, or on the no-confidence line."""
    n = [0] * (len(EDGES) + 1)
    correct = [0] * (len(EDGES) + 1)
    none = [0, 0]
    for each in judged:
        if each.confidence is None:
            none[0] += 1
            none[1] += each.correct
            continue
        index = sum(each.confidence >= edge for edge in EDGES)
        n[index] += 1
        correct[index] += each.correct
    bounds = (0.0, *EDGES, 1.0)
    return Buckets(
        buckets=tuple(
            Bucket(low=bounds[index], high=bounds[index + 1], n=n[index], correct=hits)
            for index, hits in enumerate(correct)
        ),
        no_confidence=Bucket(low=None, high=None, n=none[0], correct=none[1]),
    )


def judge_header(
    written: WrittenValues, confidence: Confidence, fields: FieldScore
) -> tuple[Judged, ...]:
    """Each distinct normalized header value read, at its higher confidence.

    `fields` is the reading scored as read, so no derived value is judged.
    """
    matched = {each.fieldtype: set(each.matched) for each in fields.per_fieldtype}
    best: dict[tuple[str, str], float | None] = {}
    for fieldtype, text, confident in _paired(written, confidence):
        value = normalize(fieldtype, text)
        if not value:
            continue
        key = (fieldtype, value)
        best[key] = _higher(best[key], confident) if key in best else confident
    return tuple(
        Judged(confident, value in matched.get(fieldtype, set()))
        for (fieldtype, value), confident in best.items()
    )


def judge_cells(
    rows: Sequence[WrittenValues],
    confidence: Confidence,
    line_items: LineItemScore,
) -> tuple[Judged, ...]:
    """Each predicted cell's distinct normalized values, judged on its paired row.

    An unpaired predicted row was scored against nothing, so none of its cells
    is matched. A cell holding several values, which Azure never returns, counts
    each distinct one at the cell's single confidence, the unit per-cell
    accuracy counts.
    """
    judged: list[Judged] = []
    for row in line_items.rows:
        if row.predicted is None:
            continue
        matched = {
            each.fieldtype: set(each.matched) for each in row.cells.per_fieldtype
        }
        cells = confidence.line_items[row.predicted : row.predicted + 1]
        confident = cells[0] if cells else {}
        for fieldtype, written in rows[row.predicted].items():
            values = {normalize(fieldtype, text) for text in _texts(written)}
            judged += (
                Judged(confident.get(fieldtype), value in matched.get(fieldtype, set()))
                for value in sorted(values)
                if value
            )
    return tuple(judged)


def gated_confidence(
    written: WrittenValues, confidence: Confidence, gated: GateResult
) -> tuple[float | None, ...]:
    """The confidence of every value a checked rule used, None where it has none.

    A used value the reading does not hold, such as a derived one, has none.
    """
    used = set(gated.used)
    paired = _paired(written, confidence)
    read = {(fieldtype, text) for fieldtype, text, _ in paired}
    return (
        *(
            confident
            for fieldtype, text, confident in paired
            if (fieldtype, text) in used
        ),
        *(None for value in used if value not in read),
    )


@dataclass(frozen=True)
class SweepLine:
    """Checked readings at one edge: wrong ones by what flagged them, false alarms."""

    edge: float
    gate_only: int
    confidence_only: int
    both: int
    neither: int
    gate_false_alarms: int
    confidence_false_alarms: int


COUNTS = (
    "gate_only",
    "confidence_only",
    "both",
    "neither",
    "gate_false_alarms",
    "confidence_false_alarms",
)
"""The counts of a sweep line, by the name of the field each fills."""


@dataclass(frozen=True)
class Sweep:
    """One line per fixed edge, and the readings with an unconfident gated value."""

    lines: tuple[SweepLine, ...]
    unconfident: int
    """Checked readings holding a gated value with no confidence, at any edge."""


@dataclass(frozen=True)
class Checked:
    """One checked reading, as the sweep needs it."""

    failed: bool
    wrong: bool
    confidence: tuple[float | None, ...]
    """Of the values its checked rules used."""


def sweep(readings: Sequence[Checked]) -> Sweep:
    """The gate beside a confidence edge, at every edge from 0.1 to 0.9."""
    lines = []
    for edge in EDGES:
        counts = dict.fromkeys(COUNTS, 0)
        for reading in readings:
            flagged = _flagged(reading.confidence, edge)
            if not reading.wrong:
                counts["gate_false_alarms"] += reading.failed
                counts["confidence_false_alarms"] += flagged
            elif reading.failed:
                counts["both" if flagged else "gate_only"] += 1
            else:
                counts["confidence_only" if flagged else "neither"] += 1
        lines.append(SweepLine(edge=edge, **counts))
    return Sweep(
        lines=tuple(lines),
        unconfident=sum(None in reading.confidence for reading in readings),
    )


def _flagged(confidence: Sequence[float | None], edge: float) -> bool:
    """Whether the lowest confidence that exists is below the edge."""
    known = [each for each in confidence if each is not None]
    return bool(known) and min(known) < edge


def _paired(
    written: WrittenValues, confidence: Confidence
) -> list[tuple[str, str, float | None]]:
    """Each header text beside the confidence at its position, blanks dropped.

    Confidence lines up with the values as the reading lists them, before the
    scorer drops a blank one; a position with no confidence has none.
    """
    paired = []
    for fieldtype, value in written.items():
        confident: Sequence[float | None] = confidence.fields.get(fieldtype, ())
        for position, text in enumerate(_listed(value)):
            if text.strip():
                at = confident[position] if position < len(confident) else None
                paired.append((fieldtype, text, at))
    return paired


def _listed(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else value


def _texts(value: str | list[str] | None) -> list[str]:
    return [text for text in _listed(value) if text.strip()]


def _higher(one: float | None, other: float | None) -> float | None:
    """The higher of two confidences, where a missing one loses to any."""
    if one is None or other is None:
        return other if one is None else one
    return max(one, other)
