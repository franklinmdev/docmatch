"""The gate: deterministic rules a reading must not break.

Models structure text and code decides, so what a backend read is checked by
rules that need no model and no label. The gate runs on a reading plus its
derived values, from a saved run, so a rule change here re-scores saved
predictions exactly.

Two rules, and only two
-----------------------

Only a rule the labels themselves pass belongs here, because a rule the labels
break rejects correct readings (#19, #23):

- **Dates sane**: `date_issue` on or before `date_due`, same day allowed.
  92.2% of UCSF label pairs pass.
- **Totals agree**: `amount_total_gross` within 0.01 of `amount_due`, after
  number normalization. 93.5% of UCSF labels pass exactly and 93.9% at a 0.5%
  tolerance, so the cent only absorbs rounding. It is not checkable when the
  reading carries `amount_paid`: then the due is what is left after a payment,
  a shape the tolerance was never measured on.

Line-total reconciliation, tax arithmetic, and identifier checksums are out:
the labels fail them, or almost no document carries what they check.

Listed values
-------------

A header field is a list. A rule holds only when every combination of the
reading's values holds: every gross within 0.01 of every due, and the latest
issue date on or before the earliest due date. A reading that disagrees with
itself fails.

A value the normalizer cannot read as a number or a calendar day makes its rule
not checkable, reported as unreadable, rather than failing it: failing would
reject correct readings for a gap in the normalizer.

The verdict
-----------

Passed when at least one rule was checked and none failed, failed when any rule
failed, and not checked when no rule could be. A reading that fails is kept and
scored as is, never retried: retrying would shop for a passing answer.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product
from typing import Literal

from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_date, read_number

RuleName = Literal["dates sane", "totals agree"]
RULES: tuple[RuleName, ...] = ("dates sane", "totals agree")
"""Every rule, in the order a report lists them."""

Outcome = Literal["passed", "failed", "absent", "paid", "unreadable"]
"""What one rule decided. The last three are the reasons it could not check."""

Verdict = Literal["passed", "failed", "not checked"]

TOLERANCE = Decimal("0.01")

UsedValue = tuple[str, str]
"""A value a rule read: its fieldtype and its text as the reading has it."""


@dataclass(frozen=True)
class RuleCheck:
    """One rule on one reading."""

    rule: RuleName
    outcome: Outcome
    used: tuple[UsedValue, ...]
    """Every value the rule compared, empty when it could not check."""


@dataclass(frozen=True)
class GateResult:
    """Both rules on one reading, and the reading's verdict."""

    checks: tuple[RuleCheck, ...]

    @property
    def verdict(self) -> Verdict:
        outcomes = {each.outcome for each in self.checks}
        if "failed" in outcomes:
            return "failed"
        return "passed" if "passed" in outcomes else "not checked"

    @property
    def used(self) -> tuple[UsedValue, ...]:
        """Every value a checked rule compared, rule by rule."""
        return tuple(value for each in self.checks for value in each.used)


def gate(header: FieldValues) -> GateResult:
    """Both rules on a reading's header, derived values already beside it."""
    return GateResult((_dates_sane(header), _totals_agree(header)))


def _dates_sane(header: FieldValues) -> RuleCheck:
    return _check(
        "dates sane",
        header,
        ("date_issue", "date_due"),
        read_date,
        lambda issued, due: max(issued) <= min(due),
    )


def _totals_agree(header: FieldValues) -> RuleCheck:
    if header.get("amount_paid"):
        return RuleCheck("totals agree", "paid", ())
    return _check(
        "totals agree",
        header,
        ("amount_total_gross", "amount_due"),
        read_number,
        lambda gross, due: all(
            abs(one - other) <= TOLERANCE for one, other in product(gross, due)
        ),
    )


def _check[T: (date, Decimal)](
    rule: RuleName,
    header: FieldValues,
    fieldtypes: tuple[str, str],
    read: Callable[[str], T | None],
    holds: Callable[[Sequence[T], Sequence[T]], bool],
) -> RuleCheck:
    """A rule over two fieldtypes' values, read by the scorer's own normalizer."""
    first = tuple(header.get(fieldtypes[0], ()))
    second = tuple(header.get(fieldtypes[1], ()))
    if not first or not second:
        return RuleCheck(rule, "absent", ())
    left = [value for value in map(read, first) if value is not None]
    right = [value for value in map(read, second) if value is not None]
    if len(left) < len(first) or len(right) < len(second):
        return RuleCheck(rule, "unreadable", ())
    used = tuple(
        (fieldtype, text)
        for fieldtype, texts in zip(fieldtypes, (first, second), strict=True)
        for text in texts
    )
    return RuleCheck(rule, "passed" if holds(left, right) else "failed", used)
