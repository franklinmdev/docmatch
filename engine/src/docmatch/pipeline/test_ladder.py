"""Tests for the routing ladder over hand-built loop runs: files only, no
Postgres and no model."""

import pytest

from docmatch.gate import Verdict
from docmatch.matching.generator import Injected
from docmatch.matching.matcher import DiscrepancyType, Place
from docmatch.pipeline.ladder import Rung, ladder, misread
from docmatch.pipeline.routing import P0, P1, P2, P3, P4, P5_AT_EACH_EDGE, RoutingReason
from docmatch.pipeline.saved import LoopRun, LoopRunError, SavedCase

LINE = Place(kind="po line", line=0)


def injected(type_: DiscrepancyType) -> Injected:
    return Injected(type=type_, place=LINE, band=None)


def case(
    document_id: str,
    *,
    reasons: tuple[RoutingReason, ...] = (),
    gate: Verdict | None = "passed",
    holds: tuple[DiscrepancyType, ...] = (),
    truth: tuple[DiscrepancyType, ...] = (),
    misread: tuple[str, ...] = (),
    gated_confidence: tuple[float | None, ...] = (),
    confidence: bool = False,
) -> SavedCase:
    return SavedCase.model_validate(
        {
            "document_id": document_id,
            "id": 1,
            "status": "needs_review" if reasons else "approved",
            "routing_reasons": reasons,
            "truth": [injected(each) for each in truth],
            "transitions": [],
            "vendor_calls": [],
            "confidence": {"fields": {}} if confidence else None,
            "gate": gate,
            "holds": holds,
            "gated_confidence": gated_confidence,
            "misread": misread,
        }
    )


def run(*cases: SavedCase) -> LoopRun:
    return LoopRun(
        backend="gemini",
        requested_model="synthetic-001",
        database_schema="loop_test",
        seed=1,
        manifest="subset.json",
        documents=len(cases),
        without_lines=(),
        cases=cases,
    )


FAILED_EXTRACTION = case(
    "failed",
    gate=None,
    truth=("price variance",),
    reasons=("extraction failed",),
)
STUCK = case(
    "stuck",
    gate=None,
    truth=("over-ship",),
    reasons=("pipeline failed at extracted",),
)
GATE_FAILED = case(
    "gate",
    gate="failed",
    misread=("amount_due",),
    reasons=("gate failed",),
)
PRICE_HELD = case(
    "price",
    holds=("price variance",),
    truth=("price variance",),
    reasons=("match held",),
)
SHORT_HELD = case(
    "short",
    holds=("short-ship",),
    truth=("short-ship",),
    reasons=("match held",),
)
CLEAN = case("clean")

EVERY_RUNG = run(FAILED_EXTRACTION, STUCK, GATE_FAILED, PRICE_HELD, SHORT_HELD, CLEAN)


def rung(rungs: tuple[Rung, ...], name: str) -> Rung:
    return next(each for each in rungs if each.policy.name == name)


def counts(found: Rung) -> tuple[int, int, int, int, int]:
    return (
        found.reviewed,
        found.approved,
        found.escaped,
        found.injected_hold,
        found.misread,
    )


def test_each_rung_reviews_more_and_lets_fewer_escape() -> None:
    rungs = ladder(EVERY_RUNG)

    assert [each.policy for each in rungs] == [P0, P1, P2, P3, P4]
    assert [counts(each) for each in rungs] == [
        (0, 6, 5, 4, 1),
        (2, 4, 3, 2, 1),
        (3, 3, 2, 2, 0),
        (4, 2, 1, 1, 0),
        (5, 1, 0, 0, 0),
    ]


def test_rates_are_over_every_document_and_over_the_approved_ones() -> None:
    p1 = rung(ladder(EVERY_RUNG), "P1")

    assert p1.review_rate == 2 / 6
    assert p1.escape_rate == 3 / 4
    assert p1.injected_hold_rate == 2 / 4
    assert p1.misread_rate == 1 / 4


def test_a_rung_that_approves_nothing_has_no_escape_rate() -> None:
    only = run(FAILED_EXTRACTION)

    p1 = rung(ladder(only), "P1")
    assert (p1.approved, p1.escape_rate) == (0, None)
    assert p1.injected_hold_rate is None


def test_a_missing_line_alone_never_escapes() -> None:
    """A missing line is a note, so approving it pays nothing it should not."""
    missing = case("missing", truth=("missing line",))

    p4 = rung(ladder(run(missing)), "P4")
    assert counts(p4) == (0, 1, 0, 0, 0)


def test_a_document_escaping_on_both_causes_counts_once_in_the_total() -> None:
    both = case("both", truth=("unit variant",), misread=("date_due",))

    p4 = rung(ladder(run(both, CLEAN)), "P4")
    assert counts(p4) == (0, 2, 1, 1, 1)
    assert p4.escape_rate == 1 / 2
    assert p4.injected_hold_rate + p4.misread_rate == 1  # type: ignore[operator]


def test_p5_is_there_only_when_the_backend_reports_confidence() -> None:
    without = ladder(run(CLEAN))
    confident = case("sure", confidence=True, gated_confidence=(0.95,))
    unsure = case(
        "unsure", confidence=True, gated_confidence=(0.45, None), misread=("date_due",)
    )

    with_edges = ladder(run(confident, unsure))

    assert "P5" not in [each.policy.name for each in without]
    assert [each.policy for each in with_edges[5:]] == list(P5_AT_EACH_EDGE)
    assert [
        (each.policy.edge, each.reviewed, each.escaped) for each in with_edges[5:]
    ] == [
        (0.1, 0, 1),
        (0.2, 0, 1),
        (0.3, 0, 1),
        (0.4, 0, 1),
        (0.5, 1, 0),
        (0.6, 1, 0),
        (0.7, 1, 0),
        (0.8, 1, 0),
        (0.9, 1, 0),
    ]


def test_p4_must_equal_what_the_loop_reached() -> None:
    """The ladder recomputes P4 from the saved outputs: a run whose saved
    status says otherwise is not one the loop routed."""
    approved_held = case("held", holds=("over-ship",))

    with pytest.raises(LoopRunError, match="held: the loop reached approved"):
        ladder(run(approved_held))


def test_p4_must_give_the_reasons_the_loop_wrote() -> None:
    wrong_reason = case(
        "wrong",
        gate="failed",
        reasons=("match held",),
    )

    with pytest.raises(LoopRunError, match="wrong: the loop reached needs_review"):
        ladder(run(wrong_reason))


def test_the_pipeline_failed_shortcut_keeps_the_reasons_known_where_it_stood() -> None:
    stuck_held = case(
        "stuck held",
        holds=("price variance",),
        reasons=(
            "match held",
            "pipeline failed at matched",
        ),
    )

    assert [each.reviewed for each in ladder(run(stuck_held))] == [0, 1, 1, 1, 1]


LABELED = {
    "date_issue": ("2026-01-05",),
    "date_due": ("2026-02-05",),
    "amount_total_gross": ("110.00",),
    "amount_due": ("110.00",),
    "vendor_name": ("Lakeside Tools",),
}


def test_a_reading_the_labels_agree_with_misreads_nothing() -> None:
    assert misread(LABELED, LABELED) == ()


def test_a_gate_value_its_label_does_not_have_is_misread() -> None:
    """Normalized, the way field F1 compares, so a format alone is no misread."""
    reading = {
        **LABELED,
        "date_issue": ("5 Jan 2026",),
        "amount_due": ("100.00",),
        "amount_total_gross": ("110.00", "11.00"),
    }

    assert misread(reading, LABELED) == ("amount_total_gross", "amount_due")


def test_only_the_gates_fieldtypes_can_be_misread() -> None:
    reading = {**LABELED, "vendor_name": ("Lakeview Tools",)}

    assert misread(reading, LABELED) == ()


def test_a_gate_value_left_unread_is_no_misread() -> None:
    """Nothing read is nothing paid on a wrong value."""
    reading = {k: v for k, v in LABELED.items() if k != "amount_due"}

    assert misread(reading, LABELED) == ()


def test_a_gate_value_read_where_nothing_is_labeled_is_misread() -> None:
    unlabeled = {k: v for k, v in LABELED.items() if k != "date_due"}

    assert misread(LABELED, unlabeled) == ("date_due",)
