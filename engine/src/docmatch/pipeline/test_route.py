"""Tests for routing at `matched` and the lease, pure: no database, no backend."""

from docmatch.extraction import azure, gemini, openai
from docmatch.extraction.run import ATTEMPTS, BACKOFF
from docmatch.gate import gate
from docmatch.matching.matcher import match
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.pipeline.loop import LEASE, route

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
    assert route(NOT_CHECKED, missing_only) == ()


def test_a_passing_gate_and_a_clean_match_route_nowhere() -> None:
    clean = match(Record(header={}, lines=(GLOVES,)), ORDER, NOTHING_RECEIVED)

    assert route(PASSED, clean) == ()


def test_every_reason_is_attached_at_once() -> None:
    held = match(Record(header={}, lines=(DEARER,)), ORDER, NOTHING_RECEIVED)

    assert route(FAILED, held) == ("gate failed", "match held")


def test_a_lease_outlasts_the_slowest_bounded_extraction() -> None:
    """Every attempt of every backend ends by its timeout, so three of them
    with the backoffs between are the longest a live extraction holds a lease."""
    slowest = max(gemini.TIMEOUT / 1000, openai.TIMEOUT, azure.TIMEOUT)
    backoffs = sum(BACKOFF * 2**each for each in range(ATTEMPTS - 1))

    assert LEASE.total_seconds() > ATTEMPTS * slowest + backoffs
