"""The gate: deterministic rules a reading must not break.

Models structure text and code decides, so what a backend read is checked by
rules that need no model and no label. The gate runs on a reading plus its
derived values, from a saved run, so a rule change here re-scores saved
predictions exactly.

Two neutral rules
-----------------

Only a rule the labels themselves pass belongs here, because a rule the labels
break rejects correct readings (#19, #23):

- **Dates sane**: `date_issue` on or before `date_due`, same day allowed.
  92.2% of UCSF label pairs pass.
- **Totals agree**: `amount_total_gross` within 0.01 of `amount_due`, after
  number normalization. 93.5% of UCSF labels pass exactly and 93.9% at a 0.5%
  tolerance, so the cent only absorbs rounding. It is not checkable when the
  reading carries `amount_paid`: then the due is what is left after a payment,
  a shape the tolerance was never measured on.

Line-total reconciliation and tax arithmetic are out: the labels fail them.
Identifier checksums are out of the neutral rules, since almost no DocILE
document carries one; a region adds them.

Regional rules
--------------

A region adds rules to the two above, and the verdict stays one (#214, #216).
For `do`, through python-stdnum 2.2 as published, offline, each on one
fieldtype:

- **vendor RNC** and **customer RNC**: `vendor_tax_id` and `customer_tax_id`
  pass when the value is a valid RNC (mod 11) or a valid cedula (Luhn), each
  with stdnum's whitelist. stdnum's `compact` strips only spaces and hyphens,
  so a letter fails. dgii-ts's prefix rules are not added.
- **NCF** and **modified NCF**: `document_id` and `document_id_modified` pass
  when the value is an NCF or e-NCF of a known shape and type code. No
  checksum exists. stdnum's NCF `compact` strips spaces only, so a hyphen
  written inside an NCF fails.

`check_dgii` and `search_dgii` are never called. A rule is admitted only when
every label of the set it checks passes it (`admit`), so a stdnum edge case
shows on the owner's own comprobantes before it moves a number; the regional
`eval` prints each admission. A set with no region runs no regional rule, so
its gate pass rates never move.

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

from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product
from typing import Literal

from stdnum.do import cedula, ncf, rnc

from docmatch.metrics.fields import FieldValues
from docmatch.metrics.normalization import read_date, read_number

RuleName = Literal[
    "dates sane",
    "totals agree",
    "vendor RNC",
    "customer RNC",
    "NCF",
    "modified NCF",
]
RULES: tuple[RuleName, ...] = ("dates sane", "totals agree")
"""Every neutral rule, in the order a report lists them. A regional rule is
never unreadable, so the eval's unreadable counts run over these alone."""

Outcome = Literal["passed", "failed", "absent", "paid", "unreadable"]
"""What one rule decided. The last three are the reasons it could not check."""

Verdict = Literal["passed", "failed", "not checked"]

TOLERANCE = Decimal("0.01")

DATES = ("date_issue", "date_due")
TOTALS = ("amount_total_gross", "amount_due")
FIELDTYPES: tuple[str, ...] = DATES + TOTALS
"""Every fieldtype whose values a rule compares."""

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


@dataclass(frozen=True)
class RegionalRule:
    """One fieldtype's identifier check, added by a region."""

    name: RuleName
    fieldtype: str
    valid: Callable[[str], bool]


@dataclass(frozen=True)
class RuleAdmission:
    """How a regional rule fared against a set's own labels."""

    rule: RegionalRule
    labeled: int
    """Documents whose labels carry a value of the rule's fieldtype."""
    broken: int
    """Of those, documents with a label the rule fails."""

    @property
    def admitted(self) -> bool:
        return self.broken == 0


def _rnc_or_cedula(number: str) -> bool:
    return rnc.is_valid(number) or cedula.is_valid(number)


_DO: tuple[RegionalRule, ...] = (
    RegionalRule("vendor RNC", "vendor_tax_id", _rnc_or_cedula),
    RegionalRule("customer RNC", "customer_tax_id", _rnc_or_cedula),
    RegionalRule("NCF", "document_id", ncf.is_valid),
    RegionalRule("modified NCF", "document_id_modified", ncf.is_valid),
)

REGIONAL_RULES: dict[str, tuple[RegionalRule, ...]] = {"do": _DO}
"""Every region's rules, before admission."""


def rules_of(region: str | None) -> tuple[RegionalRule, ...]:
    """The rules a region adds; none for a neutral set or a region with none."""
    return REGIONAL_RULES.get(region or "", ())


def admit(
    region: str | None,
    labeled: Iterable[FieldValues],
    coverage: Collection[str] | None = None,
) -> tuple[RuleAdmission, ...]:
    """Each of a region's rules against a set's labels, one entry per rule, counted
    in documents.

    A rule on a fieldtype the labels do not cover is left out: nobody labeled
    it, so nothing says the rule holds.

    A rule no label carries a value for breaks nothing and is admitted, with
    its zero count printed beside it.
    """
    documents = list(labeled)
    admissions = []
    for rule in rules_of(region):
        if coverage is not None and rule.fieldtype not in coverage:
            continue
        values = [each.get(rule.fieldtype, ()) for each in documents]
        admissions.append(
            RuleAdmission(
                rule,
                labeled=sum(1 for each in values if each),
                broken=sum(1 for each in values if not all(map(rule.valid, each))),
            )
        )
    return tuple(admissions)


def gate(header: FieldValues, regional: Sequence[RegionalRule] = ()) -> GateResult:
    """Both neutral rules and the admitted regional ones on a reading's header,
    derived values already beside it."""
    return GateResult(
        (
            _dates_sane(header),
            _totals_agree(header),
            *(_identifier(header, each) for each in regional),
        )
    )


def _identifier(header: FieldValues, rule: RegionalRule) -> RuleCheck:
    """Every value of the rule's fieldtype must pass; none leaves it absent."""
    values = tuple(header.get(rule.fieldtype, ()))
    if not values:
        return RuleCheck(rule.name, "absent", ())
    used = tuple((rule.fieldtype, each) for each in values)
    return RuleCheck(
        rule.name, "passed" if all(map(rule.valid, values)) else "failed", used
    )


def _dates_sane(header: FieldValues) -> RuleCheck:
    return _check(
        "dates sane",
        header,
        DATES,
        read_date,
        lambda issued, due: max(issued) <= min(due),
    )


def _totals_agree(header: FieldValues) -> RuleCheck:
    if header.get("amount_paid"):
        return RuleCheck("totals agree", "paid", ())
    return _check(
        "totals agree",
        header,
        TOTALS,
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
