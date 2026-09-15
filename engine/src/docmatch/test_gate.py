"""Tests for the gate: the two rules a reading must not break, and its verdict.

What the gate is worth against the labels is pinned in `evals/test_run.py`,
over the synthetic corpus; these pin what each rule decides on a reading.
"""

from collections.abc import Mapping, Sequence

import pytest

from docmatch.gate import GateResult, RuleCheck, gate


def reading(**fields: str | Sequence[str]) -> Mapping[str, tuple[str, ...]]:
    return {
        fieldtype: (value,) if isinstance(value, str) else tuple(value)
        for fieldtype, value in fields.items()
    }


def check(result: GateResult, rule: str) -> RuleCheck:
    return next(each for each in result.checks if each.rule == rule)


def test_dates_are_sane_when_the_issue_date_is_on_or_before_the_due_date() -> None:
    same_day = gate(reading(date_issue="03/04/26", date_due="March 4, 2026"))

    assert check(same_day, "dates sane").outcome == "passed"


def test_dates_are_not_sane_when_the_invoice_is_due_before_it_was_issued() -> None:
    result = gate(reading(date_issue="03/05/26", date_due="03/04/26"))

    assert check(result, "dates sane").outcome == "failed"


def test_totals_agree_within_a_cent_after_number_normalization() -> None:
    result = gate(reading(amount_total_gross="US$ 1,320.00", amount_due="1.320,01"))

    assert check(result, "totals agree").outcome == "passed"


def test_totals_do_not_agree_past_a_cent() -> None:
    result = gate(reading(amount_total_gross="1320.00", amount_due="1320.02"))

    assert check(result, "totals agree").outcome == "failed"


def test_totals_are_not_checked_when_the_reading_carries_an_amount_paid() -> None:
    """A paid invoice's due is gross minus paid, a shape the tolerance was not
    measured on."""
    result = gate(
        reading(amount_total_gross="100.00", amount_due="0.00", amount_paid="100.00")
    )

    assert check(result, "totals agree").outcome == "paid"


@pytest.mark.parametrize(
    "fields",
    [{"amount_total_gross": "100.00"}, {"amount_due": "100.00"}, {}],
)
def test_totals_are_not_checked_when_the_reading_lacks_either_total(
    fields: dict[str, str],
) -> None:
    assert check(gate(reading(**fields)), "totals agree").outcome == "absent"


def test_dates_are_not_checked_when_the_reading_lacks_either_date() -> None:
    result = gate(reading(date_issue="03/04/26"))

    assert check(result, "dates sane").outcome == "absent"


def test_every_gross_must_agree_with_every_due() -> None:
    """A reading that disagrees with itself fails."""
    result = gate(reading(amount_total_gross=["100.00", "110.00"], amount_due="100.00"))

    assert check(result, "totals agree").outcome == "failed"


def test_the_latest_issue_date_must_be_on_or_before_the_earliest_due_date() -> None:
    result = gate(
        reading(
            date_issue=["03/01/26", "03/10/26"],
            date_due=["04/01/26", "03/05/26"],
        )
    )

    assert check(result, "dates sane").outcome == "failed"


def test_listed_values_that_all_hold_pass() -> None:
    result = gate(
        reading(
            date_issue=["03/01/26", "March 1, 2026"],
            date_due=["04/01/26", "05/01/26"],
        )
    )

    assert check(result, "dates sane").outcome == "passed"


@pytest.mark.parametrize(
    ("fields", "rule"),
    [
        ({"date_issue": "03/04/26", "date_due": "EOM"}, "dates sane"),
        ({"amount_total_gross": "one hundred", "amount_due": "100.00"}, "totals agree"),
    ],
)
def test_a_value_the_normalizer_cannot_read_makes_its_rule_unreadable(
    fields: dict[str, str], rule: str
) -> None:
    """Failing it instead would reject correct readings for a normalizer gap."""
    assert check(gate(reading(**fields)), rule).outcome == "unreadable"


def test_one_unreadable_value_among_readable_ones_makes_the_rule_unreadable() -> None:
    result = gate(reading(date_issue="03/04/26", date_due=["04/03/26", "EOM"]))

    assert check(result, "dates sane").outcome == "unreadable"


def test_passes_when_a_rule_was_checked_and_none_failed() -> None:
    result = gate(reading(date_issue="03/04/26", date_due="04/03/26"))

    assert result.verdict == "passed"


def test_fails_when_any_rule_failed() -> None:
    result = gate(
        reading(
            date_issue="03/04/26",
            date_due="04/03/26",
            amount_total_gross="100.00",
            amount_due="90.00",
        )
    )

    assert result.verdict == "failed"


def test_is_not_checked_when_no_rule_could_be_checked() -> None:
    result = gate(reading(date_issue="03/04/26", date_due="EOM"))

    assert result.verdict == "not checked"


def test_names_the_values_every_checked_rule_used_and_no_others() -> None:
    """The ablation judges a reading wrong only on what the gate looked at."""
    result = gate(
        reading(
            date_issue="03/04/26",
            date_due="04/03/26",
            amount_total_gross="100.00",
            vendor_name="Acme",
        )
    )

    assert result.used == (("date_issue", "03/04/26"), ("date_due", "04/03/26"))
