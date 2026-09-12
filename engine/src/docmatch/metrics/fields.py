"""Scoring one set of fields against another, and reading a prediction file.

A label and a prediction are both a fieldtype with one or more values, so the
score is a comparison of two mappings, one fieldtype at a time. Every value
goes through `normalization` first, so the number measures extraction rather
than formatting.

A document's KILE header is one such set. So is a single line item, whose cells
are fields carrying LIR fieldtypes, which is why `score_fields` and
`by_fieldtype` are written in terms of fields rather than headers and why
`line_items` scores a row by calling them. What is particular to the header
lives here; what is particular to a table lives there.

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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from docmatch.docile.annotation import Annotation, FieldExtraction
from docmatch.metrics.normalization import normalize
from docmatch.metrics.score import Score

FieldValues = Mapping[str, Sequence[str]]
"""Fields as a scorer takes them: a fieldtype and every value under it."""

WrittenValues = dict[str, str | list[str] | None]
"""Fields as a hand-written file writes them: one value, a list, or `null`."""


class Prediction(BaseModel):
    """What a run claims one document says: its header fields and its rows.

    `{"fields": {...}, "line_items": [{...}, {...}]}`, where each of those
    objects carries a fieldtype to one value, a list of values, or `null` for a
    field the document does not have. Either part may be left out, which is a
    claim that the document carries none of it, not a claim that it was not
    looked at. Any other key is rejected rather than ignored, so a file that
    puts fieldtypes at the top level is reported as the mistake it is instead
    of scoring as an empty prediction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fields: WrittenValues = {}
    line_items: tuple[WrittenValues, ...] = ()

    @property
    def header(self) -> FieldValues:
        """The predicted header as the field scorer takes it."""
        return _values(self.fields)

    @property
    def rows(self) -> tuple[FieldValues, ...]:
        """The predicted line items as the line-item scorer takes them."""
        return tuple(_values(row) for row in self.line_items)


def _values(written: WrittenValues) -> dict[str, tuple[str, ...]]:
    """One written object as field values, with absences kept as empty."""
    return {
        fieldtype: () if value is None else _texts(value)
        for fieldtype, value in written.items()
    }


def _texts(value: str | list[str]) -> tuple[str, ...]:
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
            f'{path} is not a prediction: expected a JSON object with "fields", a '
            f'fieldtype to one value, a list of values, or null, and "line_items", a '
            f"list of those. {first_problem(error)}"
        ) from error


def first_problem(error: ValidationError, within: int = 0) -> str:
    """The first thing pydantic objected to, named by where it is in the file.

    The path is spelled out to the fieldtype, `line_items.0.line_item_quantity`
    rather than `line_items`, because a prediction nests and the half it went
    wrong in is not enough to find it by. It stops there: a value that fits no
    branch of `str | list[str] | None` is reported once per branch, and each
    error carries the branch it failed on as one more step of the path, which
    names pydantic's union rather than anything in the file.

    `within` is how many leading steps name the thing holding the prediction
    rather than the prediction, which is one document id for a whole run's
    predictions and nothing for a single document's.
    """
    first = error.errors()[0]
    loc = first["loc"]
    nests = within + (3 if loc[within : within + 1] == ("line_items",) else 2)
    where = ".".join(str(part) for part in loc[:nests])
    named = f"{where} is the first problem" if where else "The file itself"
    return f"{named}: {first['msg'].lower()}."


def by_fieldtype(fields: Iterable[FieldExtraction]) -> dict[str, tuple[str, ...]]:
    """Labels grouped by fieldtype, in document order, as a scorer takes them.

    DocILE localizes every occurrence, so a fieldtype may appear more than
    once, whether the labels are a document's header or one line item's cells.
    """
    grouped: dict[str, tuple[str, ...]] = {}
    for field in fields:
        grouped[field.fieldtype] = (*grouped.get(field.fieldtype, ()), field.text)
    return grouped


def labeled_fields(annotation: Annotation) -> dict[str, tuple[str, ...]]:
    """The document's KILE labels grouped by fieldtype, in document order."""
    return by_fieldtype(annotation.fields)


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
class FieldScore(Score):
    """One set of fields scored against another, per fieldtype and in aggregate."""

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
