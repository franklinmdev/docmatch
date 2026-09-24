"""Tests for routing at `matched`, pure: no database, no backend."""

from docmatch.gate import gate
from docmatch.matching.matcher import match
from docmatch.matching.records import ReceivingRecord, Record
from docmatch.pipeline.loop import route

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
