"""Scoring a predicted set of KILE header fields against the labeled set.

A label and a prediction are both a fieldtype with one or more values, so the
score is a comparison of two mappings, one fieldtype at a time. Every value
goes through `normalization` first, so the number measures extraction rather
than formatting.

Absence is explicit on both sides. A fieldtype the label carries and the
prediction does not is a miss; one the prediction carries and the label does
not is a false positive. A prediction may also say `null` or `""` to mean the
field is not on the document, which is written down as an absence and is
neither predicted nor wrong.

Multi-valued fieldtypes
-----------------------

DocILE localizes every occurrence of a value on the page, so a document whose
vendor name is printed in the letterhead and again in the footer carries
`vendor_name` twice. An extractor reports values, not occurrences, and 6,929 of
the 9,836 repeated fieldtypes in the annotated set are one value written twice.
Both sides are therefore reduced to their distinct normalized values before
they are compared: a value labeled twice has to be predicted once, and a
fieldtype with two genuinely different labeled values, such as two tax rates,
needs both predicted for full recall.

The one repetition this does not reach is inside a single label. Where a box is
drawn around two letterhead lines that repeat the customer's name, the label's
own text carries the name twice, and a prediction of the name once is scored as
both a miss and a false positive. That is 160 of the 71,513 labels, 0.22%, and
it is left alone on purpose: collapsing repeated lines inside a value would be
a guess about layout, and the cost falls on every backend equally, so it does
not move the comparison this benchmark exists to make.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import RootModel, ValidationError

from docmatch.docile.annotation import Annotation
from docmatch.metrics.normalization import normalize

FieldValues = Mapping[str, Sequence[str]]
"""Header fields as the scorer takes them: a fieldtype and every value under it."""


class Prediction(RootModel[dict[str, str | list[str] | None]]):
    """The fields a run claims a document carries, as a hand-written JSON object.

    One value, a list of values, or `null` for a field the document does not
    carry: `{"vendor_name": "Acme", "tax_detail_rate": ["8.25%", "5%"],
    "date_due": null}`.
    """

    @property
    def fields(self) -> dict[str, tuple[str, ...]]:
        """The prediction as field values, with absences kept as empty."""
        return {
            fieldtype: () if value is None else _values(value)
            for fieldtype, value in self.root.items()
        }


def _values(value: str | list[str]) -> tuple[str, ...]:
    texts = [value] if isinstance(value, str) else value
    return tuple(text for text in texts if text.strip())


class PredictionError(Exception):
    """The prediction file cannot be read, or is not a prediction."""


def read_prediction(path: Path) -> Prediction:
    """A hand-written prediction file, or a message saying what is wrong with it."""
    try:
        return Prediction.model_validate_json(path.read_bytes())
    except OSError as error:
        raise PredictionError(f"cannot read the prediction {path}: {error}") from error
    except ValidationError as error:
        raise PredictionError(
            f"{path} is not a prediction: expected a JSON object of fieldtype to one "
            f"value, a list of values, or null. {_first_problem(error)}"
        ) from error


def _first_problem(error: ValidationError) -> str:
    """The first thing pydantic objected to, named by where it is in the file."""
    first = error.errors()[0]
    fieldtype = next((str(part) for part in first["loc"]), None)
    where = f"{fieldtype} is the first problem" if fieldtype else "The file itself"
    return f"{where}: {first['msg'].lower()}."


def labeled_fields(annotation: Annotation) -> dict[str, tuple[str, ...]]:
    """The document's KILE labels grouped by fieldtype, in document order."""
    grouped: dict[str, tuple[str, ...]] = {}
    for field in annotation.fields:
        grouped[field.fieldtype] = (*grouped.get(field.fieldtype, ()), field.text)
    return grouped


@dataclass(frozen=True)
class FieldTypeScore:
    """How one fieldtype went, in normalized values so a human can see why."""

    fieldtype: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    """Labeled and not predicted."""
    spurious: tuple[str, ...]
    """Predicted and not labeled."""

    @property
    def is_empty(self) -> bool:
        return not (self.matched or self.missing or self.spurious)


@dataclass(frozen=True)
class FieldScore:
    """One document's field score, per fieldtype and in aggregate.

    The counts are the part that aggregates across documents; the ratios are
    derived from them, so a later micro-average sums counts rather than means.
    """

    per_fieldtype: tuple[FieldTypeScore, ...]

    @property
    def true_positives(self) -> int:
        return sum(len(each.matched) for each in self.per_fieldtype)

    @property
    def false_negatives(self) -> int:
        return sum(len(each.missing) for each in self.per_fieldtype)

    @property
    def false_positives(self) -> int:
        return sum(len(each.spurious) for each in self.per_fieldtype)

    @property
    def precision(self) -> float:
        """Of what was predicted, how much was labeled. Vacuously 1.0 if nothing was."""
        return _ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float:
        """Of what was labeled, how much was predicted. Vacuously 1.0 if nothing was."""
        return _ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> float:
        precision, recall = self.precision, self.recall
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)


def _ratio(part: int, whole: int) -> float:
    return 1.0 if whole == 0 else part / whole


def score_fields(labeled: FieldValues, predicted: FieldValues) -> FieldScore:
    """One document's field score, fieldtype by fieldtype.

    A fieldtype left with no values on either side, because the prediction
    wrote it down as absent and the label does not carry it, is dropped: it is
    an agreement about nothing and has no bearing on any count.
    """
    scored = (
        _score_one(fieldtype, labeled.get(fieldtype, ()), predicted.get(fieldtype, ()))
        for fieldtype in sorted({*labeled, *predicted})
    )
    return FieldScore(tuple(each for each in scored if not each.is_empty))


def _score_one(
    fieldtype: str, labeled: Sequence[str], predicted: Sequence[str]
) -> FieldTypeScore:
    gold = _distinct(fieldtype, labeled)
    guess = _distinct(fieldtype, predicted)
    return FieldTypeScore(
        fieldtype=fieldtype,
        matched=tuple(sorted(gold & guess)),
        missing=tuple(sorted(gold - guess)),
        spurious=tuple(sorted(guess - gold)),
    )


def _distinct(fieldtype: str, values: Sequence[str]) -> frozenset[str]:
    """The distinct normalized values, dropping any that normalize to nothing."""
    normalized = (normalize(fieldtype, value) for value in values)
    return frozenset(value for value in normalized if value)
