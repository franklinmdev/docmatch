"""Recomputing the counts this engine's rules are justified by.

Several decisions in `metrics` were settled by measurement rather than by
assumption: which way an ambiguous numeric date reads, what a lone dot means,
whether a fieldtype labeled twice carries one value or two. Each of those
numbers is written down beside the rule it justifies, and none of them could be
checked from a clean checkout, because the scripts that produced them were
thrown away. A reader could not verify them and nothing said when a rule change
made one stale.

This module is that script, kept. `docmatch corpus` runs it over the annotated
set and prints every count the docstrings assert, grouped by the module that
asserts it, so the two can be compared by eye. It is a survey of the corpus and
of what the rules make of it; it is not a second eval, and it scores nothing.

Counts, not values
------------------

The report carries counts, shares and fieldtype names, and never a label. Rule
6 of `CLAUDE.md` is why, and `evals.run` prints its breakdowns the same way.

What a probe may assume
-----------------------

Every probe asks the rules themselves rather than reimplementing them, so a
rule change moves the number instead of leaving it behind: coverage is
`normalization.reads`, the repeated values are `normalization.normalize`, and
the fieldtypes of a row are `fields.by_fieldtype`. The two probes that cannot
do that are the ones asking what an alternative rule would have made of the
corpus, and each says so where it is written.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from docmatch.docile.annotation import Annotation, FieldExtraction
from docmatch.metrics.fields import by_fieldtype
from docmatch.metrics.normalization import (
    MONTHS,
    Rule,
    names_a_month,
    normalize,
    normalize_text,
    prepared_date,
    prepared_number,
    reads,
    reads_number,
    rule,
)
from docmatch.metrics.score import ratio

SPLIT = "trainval"
"""The annotated set: train and val together, which every count is over."""

Census = tuple[tuple[str, int], ...]
"""How something is spread over the fieldtypes carrying it, largest first."""


@dataclass(frozen=True)
class Corpus:
    """What the annotated set holds, before any rule is applied to it."""

    documents: int
    fields: int
    """KILE labels: header values, localized once per occurrence."""
    cells: int
    """LIR labels: the cells of every line-item table."""
    rows: int
    without_a_table: int
    """Documents carrying no line items at all."""
    largest_table: int
    """Rows in the largest table, which is the widest assignment ever solved."""


@dataclass(frozen=True)
class Coverage:
    """How much of one fieldtype the rules can read."""

    fieldtype: str
    rule: Rule
    header: bool
    """Whether this is a KILE fieldtype; the rest are a line item's cells."""
    labels: int
    read: int
    """Labels the fieldtype's own rule reads rather than handing to the text rule."""
    as_a_number: int
    """Labels the number rule would read, whether or not that is this rule."""

    @property
    def share(self) -> float:
        return ratio(self.read, self.labels)

    @property
    def number_share(self) -> float:
        return ratio(self.as_a_number, self.labels)


@dataclass(frozen=True)
class RuleCoverage:
    """How much of everything one rule is given it can read."""

    rule: Rule
    labels: int
    read: int

    @property
    def share(self) -> float:
        return ratio(self.read, self.labels)


@dataclass(frozen=True)
class Dates:
    """What the corpus says about the two guesses the date rule has to make."""

    month_first: int
    """Numeric dates whose second number is past the twelfth, so it is a day."""
    day_first: int
    """Numeric dates whose first number is past the twelfth, so it is a day."""
    three_letter_months: int
    """Date labels the strictness of the month rule turns on.

    Each is a label the rule leaves as text although every word in it would
    name a month by its first three letters. Some are a month the rule should
    have read, such as a German `JUNI`; the rest are what the strictness buys,
    a word like `MAYBE` that the looser rule would read as May. Which is which
    is a judgement about a document, so the count is of both together.
    """


@dataclass(frozen=True)
class Repeats:
    """The repetition the two scorers had to decide what to do with."""

    fieldtypes: int
    """Header fieldtypes carrying more than one label in one document."""
    one_value: int
    """Of those, the ones whose labels are all one value after normalization."""
    within_a_label: int
    """Labels whose own text repeats a line, which no scorer can reach."""
    rows: int
    """Line items carrying more than one cell of the same fieldtype."""
    rows_after_normalization: int
    """Of those, the ones still carrying two different values."""


@dataclass(frozen=True)
class Survey:
    """Every count the docstrings assert, over one split of the dataset."""

    split: str
    corpus: Corpus
    coverage: tuple[Coverage, ...]
    lone_dot: Census
    """Number labels written with a lone dot and exactly three digits after it.

    Only the fieldtypes the number rule reads: a dot in an identifier is text
    either way, so it has no bearing on what a dot means.
    """
    dates: Dates
    repeats: Repeats

    @property
    def header_rules(self) -> tuple[RuleCoverage, ...]:
        """Coverage per rule over header labels."""
        return self._by_rule(header=True)

    @property
    def cell_rules(self) -> tuple[RuleCoverage, ...]:
        """Coverage per rule over line-item cells."""
        return self._by_rule(header=False)

    def _by_rule(self, *, header: bool) -> tuple[RuleCoverage, ...]:
        grouped: dict[Rule, list[Coverage]] = {}
        for each in self.coverage:
            if each.header == header:
                grouped.setdefault(each.rule, []).append(each)
        return tuple(
            RuleCoverage(
                rule=name,
                labels=sum(each.labels for each in group),
                read=sum(each.read for each in group),
            )
            for name, group in sorted(grouped.items())
        )


def survey(annotations: Iterable[Annotation], split: str) -> Survey:
    """Every count, in one pass over the annotations of a split."""
    tally = _Tally()
    for annotation in annotations:
        tally.add(annotation)
    return tally.survey(split)


@dataclass
class _Counts:
    """What one fieldtype's labels are, in one part of the document."""

    labels: int = 0
    read: int = 0
    as_a_number: int = 0


@dataclass
class _Tally:
    """The survey while it is still being counted, one document at a time."""

    documents: int = 0
    rows: int = 0
    without_a_table: int = 0
    largest_table: int = 0
    counted: dict[tuple[str, bool], _Counts] = field(default_factory=dict)
    """Per fieldtype, and per whether it is a header fieldtype or a cell."""
    lone_dot: Counter[str] = field(default_factory=Counter)
    month_first: int = 0
    day_first: int = 0
    three_letter_months: int = 0
    repeated_fieldtypes: int = 0
    repeated_one_value: int = 0
    within_a_label: int = 0
    repeating_rows: int = 0
    repeating_rows_after_normalization: int = 0

    def add(self, annotation: Annotation) -> None:
        self.documents += 1
        self._add_labels(annotation.fields, header=True)
        self._add_labels(annotation.cells, header=False)
        self._add_header(annotation.fields)
        self._add_table(annotation)

    def _add_labels(self, labels: Sequence[FieldExtraction], *, header: bool) -> None:
        """What every label is, and what the rules make of it."""
        for label in labels:
            counts = self.counted.setdefault((label.fieldtype, header), _Counts())
            counts.labels += 1
            counts.read += reads(label.fieldtype, label.text)
            counts.as_a_number += reads_number(label.text)
            if rule(label.fieldtype) == "number" and _is_lone_dot(label.text):
                self.lone_dot[label.fieldtype] += 1
            if rule(label.fieldtype) == "date":
                self._add_date(label)

    def _add_date(self, label: FieldExtraction) -> None:
        proves = _ordering(label.text)
        self.month_first += proves == "month"
        self.day_first += proves == "day"
        if not reads(label.fieldtype, label.text) and _turns_on_the_month_rule(
            label.text
        ):
            self.three_letter_months += 1

    def _add_header(self, fields: Sequence[FieldExtraction]) -> None:
        """The two kinds of repetition the field score had to decide about."""
        for fieldtype, texts in by_fieldtype(fields).items():
            if len(texts) > 1:
                self.repeated_fieldtypes += 1
                self.repeated_one_value += len(
                    {normalize(fieldtype, text) for text in texts}
                ) == 1
        self.within_a_label += sum(_repeats_a_line(each.text) for each in fields)

    def _add_table(self, annotation: Annotation) -> None:
        table = annotation.line_items
        self.rows += len(table)
        self.without_a_table += not table
        self.largest_table = max(self.largest_table, len(table))
        for item in table:
            cells = by_fieldtype(item.cells)
            if any(len(texts) > 1 for texts in cells.values()):
                self.repeating_rows += 1
                self.repeating_rows_after_normalization += any(
                    len({normalize(fieldtype, text) for text in texts}) > 1
                    for fieldtype, texts in cells.items()
                )

    def _labels(self, *, header: bool) -> int:
        return sum(
            counts.labels
            for (_, part), counts in self.counted.items()
            if part == header
        )

    def survey(self, split: str) -> Survey:
        return Survey(
            split=split,
            corpus=Corpus(
                documents=self.documents,
                fields=self._labels(header=True),
                cells=self._labels(header=False),
                rows=self.rows,
                without_a_table=self.without_a_table,
                largest_table=self.largest_table,
            ),
            coverage=tuple(
                Coverage(
                    fieldtype=fieldtype,
                    rule=rule(fieldtype),
                    header=header,
                    labels=counts.labels,
                    read=counts.read,
                    as_a_number=counts.as_a_number,
                )
                for (fieldtype, header), counts in sorted(self.counted.items())
            ),
            lone_dot=tuple(
                sorted(self.lone_dot.items(), key=lambda each: (-each[1], each[0]))
            ),
            dates=Dates(
                month_first=self.month_first,
                day_first=self.day_first,
                three_letter_months=self.three_letter_months,
            ),
            repeats=Repeats(
                fieldtypes=self.repeated_fieldtypes,
                one_value=self.repeated_one_value,
                within_a_label=self.within_a_label,
                rows=self.repeating_rows,
                rows_after_normalization=self.repeating_rows_after_normalization,
            ),
        )


_LONE_DOT = re.compile(r"[^\d.,]*\d+\.\d{3}[^\d.,]*")
"""A value whose only separator is a dot with exactly three digits after it.

That is the one shape the two readings of a dot disagree about: `123.456` is
either 123456 or a number a thousand times smaller. Anything else, including
`7.25` and `1.234.567`, means the same under both.
"""


def _is_lone_dot(text: str) -> bool:
    return _LONE_DOT.fullmatch(prepared_number(text)) is not None


def _ordering(text: str) -> str | None:
    """Which ordering an ambiguous numeric date proves, if it proves one.

    The eligibility test is `normalization._read_date`'s: three numbers, no
    words, and no leading four-digit year, which is exactly the case the rule
    has to guess at. It is spelled out again here because the rule reads a date
    and this asks what the date would have to be, which the rule never says.

    A number past the twelfth can only be a day, so it names the ordering of
    the two that come before the year.
    """
    prepared = prepared_date(text)
    if re.search(r"[A-Z]", prepared):
        return None
    numbers = [int(number) for number in re.findall(r"\d+", prepared)]
    if len(numbers) != 3 or len(str(numbers[0])) == 4:
        return None
    first, second, _ = numbers
    if first <= 12 < second:
        return "month"
    if second <= 12 < first:
        return "day"
    return None


def _turns_on_the_month_rule(text: str) -> bool:
    """Whether the month rule is why a date label was not read.

    The alternative rule is written out here because it is the one this engine
    did not take: a word names a month when its first three letters name
    exactly one. A label turns on the choice when every word in it is a month
    to that rule and at least one is not a month to this one, which leaves out
    the labels that are unread for some other reason.
    """
    words = re.findall(r"[A-Z]+", prepared_date(text))
    if not words:
        return False
    return all(_by_three_letters(word) for word in words) and any(
        names_a_month(word) is None for word in words
    )


def _by_three_letters(word: str) -> bool:
    return len(word) >= 3 and sum(name.startswith(word[:3]) for name in MONTHS) == 1


def _repeats_a_line(text: str) -> bool:
    """Whether a label's own text carries one line twice.

    A box drawn around two letterhead lines that repeat the customer's name is
    one label carrying the name twice, which the field score counts as both a
    miss and a false positive however it is predicted.
    """
    lines = [line for line in map(normalize_text, text.split("\n")) if line]
    return len(set(lines)) < len(lines)
