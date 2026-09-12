"""Precision, recall, and F1 over whatever a scorer counts.

Every metric in this package counts the same three things, a true positive, a
false negative, and a false positive, and derives the same three ratios from
them. What differs is the unit being counted: a header field value here, a
whole line item there. The counts are the part that aggregates across
documents, so a later micro-average sums counts rather than means; the ratios
are always derived.
"""

from abc import ABC, abstractmethod


class Score(ABC):
    """The three ratios, over counts a subclass defines."""

    @property
    @abstractmethod
    def true_positives(self) -> int:
        """Labeled and predicted."""

    @property
    @abstractmethod
    def false_negatives(self) -> int:
        """Labeled and not predicted."""

    @property
    @abstractmethod
    def false_positives(self) -> int:
        """Predicted and not labeled."""

    @property
    def precision(self) -> float:
        """Of what was predicted, how much was labeled. Vacuously 1.0 if nothing was."""
        return ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Of what was labeled, how much was predicted. Vacuously 1.0 if nothing was."""
        return ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)


def ratio(part: int, whole: int) -> float:
    """A share of a whole that may be nothing, in which case there is no shortfall."""
    return 1.0 if whole == 0 else part / whole
