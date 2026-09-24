"""Tests for routing under each policy and for the lease, pure: no database,
no backend."""

import pytest

from docmatch.extraction import azure, gemini, openai
from docmatch.extraction.run import ATTEMPTS, BACKOFF
from docmatch.gate import gate
from docmatch.matching.matcher import DiscrepancyType, match
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.pipeline.loop import LEASE
from docmatch.pipeline.routing import (
    CONFIDENCE,
    LADDER,
    P0,
    P1,
    P2,
    P3,
    P4,
    Shortcut,
    Standing,
    p5,
    route,
    standing,
)

GLOVES = {
    "line_item_description": ("Work gloves",),
    "line_item_amount_gross": ("50.00",),
}
HAMMER = {
    "line_item_description": ("Claw hammer",),
    "line_item_amount_gross": ("20.00",),
}
DEARER = {**GLOVES, "line_item_amount_gross": ("60.00",)}

ORDER = Record(header={}, lines=(GLOVES,))
NOTHING_RECEIVED = ReceivingRecord(())

PASSED = gate({"amount_total_gross": ("10.00",), "amount_due": ("10.00",)})
FAILED = gate({"amount_total_gross": ("10.00",), "amount_due": ("12.00",)})
NOT_CHECKED = gate({})


def test_a_gate_that_checked_nothing_and_a_note_only_match_never_route() -> None:
    """A missing line is a note, and a gate with nothing to check is no reason."""
    missing_only = match(
        Record(header={}, lines=(GLOVES,)),
        Record(header={}, lines=(GLOVES, HAMMER)),
        NOTHING_RECEIVED,
    )

    assert NOT_CHECKED.verdict == "not checked"
    assert [each.type for each in missing_only.findings] == ["missing line"]
    assert route(standing(NOT_CHECKED, missing_only), P4) == ()


def test_a_passing_gate_and_a_clean_match_route_nowhere() -> None:
    clean = match(Record(header={}, lines=(GLOVES,)), ORDER, NOTHING_RECEIVED)

    assert route(standing(PASSED, clean), P4) == ()


def test_every_reason_is_attached_at_once() -> None:
    held = match(Record(header={}, lines=(DEARER,)), ORDER, NOTHING_RECEIVED)

    assert standing(FAILED, held).holds == ("price variance",)
    assert route(standing(FAILED, held), P4) == ("gate failed", "match held")


def test_an_output_not_yet_saved_gives_no_reason() -> None:
    assert standing(None, None) == Standing()
    assert route(Standing(), P4) == ()


def test_the_ladder_runs_from_p0_to_p4_in_order() -> None:
    assert LADDER == (P0, P1, P2, P3, P4)


def test_p0_routes_nothing() -> None:
    everything = Standing(
        shortcut="extraction failed",
        gate="failed",
        holds=("price variance",),
        gated_confidence=(0.0,),
    )

    assert route(everything, P0) == ()


@pytest.mark.parametrize(
    "shortcut", ["extraction failed", "pipeline failed at validated"]
)
def test_p1_adds_both_shortcuts(shortcut: Shortcut) -> None:
    both = Standing(shortcut=shortcut, gate="failed")

    assert route(both, P1) == (shortcut,)


def test_p2_adds_a_failed_gate_and_only_a_failed_one() -> None:
    assert route(Standing(gate="failed"), P1) == ()
    assert route(Standing(gate="failed"), P2) == ("gate failed",)
    assert route(Standing(gate="not checked"), P2) == ()


@pytest.mark.parametrize("held", ["price variance", "tax mismatch"])
def test_p3_adds_a_hold_on_price_or_tax(held: DiscrepancyType) -> None:
    on = Standing(holds=(held,))

    assert route(on, P2) == ()
    assert route(on, P3) == ("match held",)


@pytest.mark.parametrize(
    "held", ["short-ship", "over-ship", "extra line", "unit variant"]
)
def test_p4_adds_every_other_hold_type(held: DiscrepancyType) -> None:
    on = Standing(holds=(held,))

    assert route(on, P3) == ()
    assert route(on, P4) == ("match held",)


def test_pipeline_failed_comes_after_the_reasons_known_where_it_stands() -> None:
    """The order the loop writes them: what the outputs give, then the failure."""
    stuck = Standing(
        shortcut="pipeline failed at matched", gate="failed", holds=("over-ship",)
    )

    assert route(stuck, P4) == (
        "gate failed",
        "match held",
        "pipeline failed at matched",
    )


def test_p5_adds_a_gated_value_below_the_edge_to_p4() -> None:
    unsure = Standing(gated_confidence=(0.95, 0.25))

    assert route(unsure, P4) == ()
    assert route(unsure, p5(0.3)) == (CONFIDENCE,)
    assert route(unsure, p5(0.2)) == ()
    assert route(Standing(holds=("short-ship",)), p5(0.5)) == ("match held",)


def test_a_gated_value_with_no_confidence_is_never_below_an_edge() -> None:
    """A missing confidence is not a low one (the sweep's own rule, #23)."""
    assert route(Standing(gated_confidence=(None,)), p5(0.9)) == ()
    assert route(Standing(gated_confidence=(None, 0.5)), p5(0.9)) == (CONFIDENCE,)


def test_a_lease_outlasts_the_slowest_bounded_extraction() -> None:
    """Every attempt of every backend ends by its timeout, so three of them
    with the backoffs between are the longest a live extraction holds a lease."""
    slowest = max(gemini.TIMEOUT / 1000, openai.TIMEOUT, azure.TIMEOUT)
    backoffs = sum(BACKOFF * 2**each for each in range(ATTEMPTS - 1))

    assert LEASE.total_seconds() > ATTEMPTS * slowest + backoffs
