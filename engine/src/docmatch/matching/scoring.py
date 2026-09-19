"""Scoring match results against the generator's truth.

The unit is the finding: a discrepancy type at a place. A finding is right
only when both agree with the truth, so the right type on the wrong line is
a miss and a false alarm at once (#66). Per-type precision and recall count
findings; the clean-case false-positive rate counts cases, where any finding
on a clean case makes it a false positive.

Truth is always the generator's. On the end-to-end row a finding caused by a
misread value is a false alarm, and an injected discrepancy the reading hides
is a miss (#67); on the per-type table the invoice is the labels, so the gap
to 1.0 is the matcher's own.

Aggregation sums counts, as `metrics.score.MicroAverage` does: a case with
three findings weighs three, and a rate is derived from the sums.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from docmatch.matching.generator import Case
from docmatch.matching.matcher import (
    TYPES,
    DiscrepancyType,
    MatchResult,
    Place,
    match,
)
from docmatch.metrics.score import MicroAverage, Score, micro_average, ratio

Found = tuple[DiscrepancyType, Place]
"""What a finding and an injection are compared as."""


@dataclass(frozen=True)
class TypeScore(Score):
    """How one discrepancy type went over every case, in counts."""

    type: DiscrepancyType
    hits: int
    """Found, and injected at that place."""
    misses: int
    """Injected, and not found at that place."""
    false_alarms: int
    """Found, and not injected at that place."""

    @property
    def n(self) -> int:
        """How many findings of the type were injected."""
        return self.hits + self.misses

    @property
    def true_positives(self) -> int:
        return self.hits

    @property
    def false_negatives(self) -> int:
        return self.misses

    @property
    def false_positives(self) -> int:
        return self.false_alarms


@dataclass(frozen=True)
class Table:
    """Every type's counts, and the clean cases' false positives."""

    per_type: tuple[TypeScore, ...]
    """One row per discrepancy type, in `TYPES` order, whether or not any was
    injected: a type with nothing injected reads with n 0."""
    clean_cases: int
    clean_false_positives: int
    """Clean cases with at least one finding."""

    @property
    def overall(self) -> MicroAverage:
        """Precision and recall over all findings, whatever their type."""
        return micro_average(self.per_type)

    @property
    def clean_false_positive_rate(self) -> float:
        """Of the clean cases, how many carried a finding; 0.0 of none."""
        return (
            0.0
            if self.clean_cases == 0
            else ratio(self.clean_false_positives, self.clean_cases)
        )


def score(cases: Sequence[Case], results: Sequence[MatchResult]) -> Table:
    """Every case's findings against its truth, summed by type."""
    hits: Counter[DiscrepancyType] = Counter()
    misses: Counter[DiscrepancyType] = Counter()
    false_alarms: Counter[DiscrepancyType] = Counter()
    clean_false_positives = 0
    for case, result in zip(cases, results, strict=True):
        truth = {(each.type, each.place) for each in case.truth}
        found = {(each.type, each.place) for each in result.findings}
        for type_, _ in truth & found:
            hits[type_] += 1
        for type_, _ in truth - found:
            misses[type_] += 1
        for type_, _ in found - truth:
            false_alarms[type_] += 1
        if case.is_clean and result.findings:
            clean_false_positives += 1
    return Table(
        per_type=tuple(
            TypeScore(type_, hits[type_], misses[type_], false_alarms[type_])
            for type_ in TYPES
        ),
        clean_cases=sum(1 for case in cases if case.is_clean),
        clean_false_positives=clean_false_positives,
    )


def score_matched(cases: Sequence[Case]) -> Table:
    """Every case matched with its own invoice, then scored."""
    return score(
        cases,
        [match(case.invoice, case.purchase_order, case.receipt) for case in cases],
    )
