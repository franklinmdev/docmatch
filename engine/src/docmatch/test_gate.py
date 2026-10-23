"""Tests for the gate: the rules a reading must not break, and its verdict.

What the gate is worth against the labels is pinned in `evals/test_run.py`,
over the synthetic corpus; these pin what each rule decides on a reading.
"""

from collections.abc import Mapping, Sequence

import pytest
from stdnum.do import rnc as stdnum_rnc

from docmatch.gate import GateResult, RuleCheck, admit, gate, rules_of


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


# Valid made-up numbers: python-stdnum 2.2 accepts each.
RNC = "130990018"
CEDULA = "00113918205"
DO = rules_of("do")


def test_a_neutral_run_has_no_regional_rules() -> None:
    assert rules_of(None) == ()
    assert gate(reading(vendor_tax_id="130990010")).checks == gate(reading()).checks


@pytest.mark.parametrize("number", [RNC, "1-30-99001-8", CEDULA, "001-1391820-5"])
def test_an_rnc_field_passes_a_valid_rnc_or_cedula(number: str) -> None:
    result = gate(reading(vendor_tax_id=number), DO)

    assert check(result, "vendor RNC").outcome == "passed"


@pytest.mark.parametrize(
    "number",
    [
        "130990019",  # RNC check digit off by one
        "00113918204",  # cedula Luhn off by one
        "13099001A",  # a letter fails stdnum's compact
        "12345",  # neither length
    ],
)
def test_an_rnc_field_fails_a_number_that_is_neither(number: str) -> None:
    result = gate(reading(customer_tax_id=number), DO)

    assert check(result, "customer RNC").outcome == "failed"
    assert result.verdict == "failed"


def test_an_rnc_stdnum_whitelists_passes_though_its_checksum_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list holds real taxpayers' numbers, so the test whitelists a made-up
    one with a bad check digit rather than quote an entry."""
    monkeypatch.setattr(stdnum_rnc, "whitelist", {*stdnum_rnc.whitelist, "130990019"})

    result = gate(reading(vendor_tax_id="130990019"), DO)

    assert check(result, "vendor RNC").outcome == "passed"


@pytest.mark.parametrize(
    "number", ["B0100000417", "E310000000052", "A020010210100000005", "b01 00000417"]
)
def test_an_ncf_field_passes_an_ncf_or_e_ncf_of_a_known_type(number: str) -> None:
    assert check(gate(reading(document_id=number), DO), "NCF").outcome == "passed"


@pytest.mark.parametrize(
    "number",
    ["B9900000417", "E990000000052", "Z0100000417", "B010000041", "E31000000005A"],
)
def test_an_ncf_field_fails_a_wrong_shape_or_type_code(number: str) -> None:
    assert check(gate(reading(document_id=number), DO), "NCF").outcome == "failed"


def test_the_modified_ncf_is_checked_as_an_ncf_apart_from_the_comprobantes_own() -> (
    None
):
    result = gate(reading(document_id="B0400000009", document_id_modified="B77"), DO)

    assert check(result, "NCF").outcome == "passed"
    assert check(result, "modified NCF").outcome == "failed"


def test_an_absent_value_leaves_its_regional_rule_not_checked() -> None:
    result = gate(reading(date_issue="03/04/26"), DO)

    assert [
        each.outcome
        for each in result.checks
        if each.rule in {"vendor RNC", "customer RNC", "NCF", "modified NCF"}
    ] == ["absent"] * 4
    assert result.verdict == "not checked"


def test_every_value_of_a_listed_field_must_pass() -> None:
    result = gate(reading(vendor_tax_id=[RNC, "130990019"]), DO)

    assert check(result, "vendor RNC").outcome == "failed"


def test_a_regional_rule_names_the_values_it_used() -> None:
    result = gate(reading(vendor_tax_id=RNC, vendor_name="Acme"), DO)

    assert result.used == (("vendor_tax_id", RNC),)


def test_one_verdict_covers_neutral_and_regional_rules() -> None:
    result = gate(
        reading(date_issue="03/04/26", date_due="04/03/26", vendor_tax_id="130990019"),
        DO,
    )

    assert check(result, "dates sane").outcome == "passed"
    assert result.verdict == "failed"


def test_a_rule_is_admitted_when_no_label_breaks_it() -> None:
    labels = [reading(vendor_tax_id=RNC, document_id="B0100000417"), reading()]

    admission = {each.rule.name: each for each in admit("do", labels)}

    assert admission["vendor RNC"].admitted
    assert (admission["vendor RNC"].labeled, admission["vendor RNC"].broken) == (1, 0)
    assert admission["customer RNC"].labeled == 0


def test_a_rule_a_label_breaks_is_not_admitted() -> None:
    labels = [reading(vendor_tax_id=RNC), reading(vendor_tax_id="130990019")]

    admission = {each.rule.name: each for each in admit("do", labels)}

    assert not admission["vendor RNC"].admitted
    assert (admission["vendor RNC"].labeled, admission["vendor RNC"].broken) == (2, 1)


def test_a_neutral_region_admits_nothing() -> None:
    assert admit(None, [reading(vendor_tax_id="x")]) == ()


def test_a_rule_on_a_fieldtype_the_labels_do_not_cover_is_not_considered() -> None:
    covered = ("vendor_tax_id", "document_id")

    names = [each.rule.name for each in admit("do", [reading()], covered)]

    assert names == ["vendor RNC", "NCF"]
