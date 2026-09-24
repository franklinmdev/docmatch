"""Tests for the pipeline report over hand-built loop runs: files only, no
Postgres and no model."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from docmatch.pipeline.loop import PENDING, Status
from docmatch.pipeline.report import report
from docmatch.pipeline.saved import (
    LoopRun,
    LoopRunError,
    SavedCall,
    SavedCase,
    SavedTransition,
    read_loop_run,
    write_loop_run,
)

START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def moved(
    from_status: Status | None,
    to: Status,
    taken: float | None,
    committed: float,
    *reasons: str,
) -> SavedTransition:
    return SavedTransition(
        from_status=from_status,
        to=to,
        taken_at=None if taken is None else at(taken),
        committed_at=at(committed),
        actor="system",
        reasons=reasons,
    )


def call(status: Status, cost: str, attempt: int = 1) -> SavedCall:
    return SavedCall(
        status=status,
        attempt=attempt,
        units={"input_tokens": 1000},
        cost=Decimal(cost),
        served_model="synthetic-001-a",
        started_at=at(0),
        ended_at=at(1),
        failed=False,
    )


def approved(document_id: str, offset: float, *calls: SavedCall) -> SavedCase:
    """A case the system approved, each status a second of wait then a second
    of work, extraction a second longer."""
    t = offset
    return SavedCase(
        document_id=document_id,
        id=1,
        status="approved",
        routing_reasons=(),
        truth=(),
        transitions=(
            moved(None, "received", None, t),
            moved("received", "extracted", t + 1, t + 3),
            moved("extracted", "validated", t + 4, t + 5),
            moved("validated", "resolved", t + 6, t + 7),
            moved("resolved", "matched", t + 8, t + 9),
            moved("matched", "approved", t + 10, t + 11),
        ),
        vendor_calls=calls,
    )


def failed_extraction(document_id: str, *calls: SavedCall) -> SavedCase:
    return SavedCase(
        document_id=document_id,
        id=2,
        status="needs_review",
        routing_reasons=("extraction failed",),
        truth=(),
        transitions=(
            moved(None, "received", None, 0),
            moved("received", "needs_review", 2, 20, "extraction failed"),
        ),
        vendor_calls=calls,
    )


def run(*cases: SavedCase, source: str | None = None) -> LoopRun:
    return LoopRun(
        backend="replay" if source else "gemini",
        source=source,
        requested_model="synthetic-001",
        database_schema="loop_test",
        seed=1,
        manifest="subset.json",
        documents=len(cases) + 1,
        without_lines=("eval0002",),
        cases=cases,
    )


def test_end_to_end_runs_from_the_upload_to_the_systems_settling() -> None:
    reported = report(run(approved("a", 0), failed_extraction("b")))

    assert sorted(reported.end_to_end_seconds) == [11.0, 20.0]
    assert reported.end_to_end_spread.p50 == 11.0
    assert reported.end_to_end_spread.p95 == 20.0


def test_each_status_reads_wait_and_work_apart() -> None:
    reported = report(run(approved("a", 0), failed_extraction("b")))

    received = reported.status("received")
    assert received.wait is not None and received.work is not None
    assert (received.wait.p50, received.wait.p95) == (1.0, 2.0)
    assert (received.work.p50, received.work.p95) == (2.0, 18.0)
    matched = reported.status("matched")
    assert matched.wait is not None and matched.work is not None
    assert (matched.wait.p50, matched.work.p50) == (1.0, 1.0)


def test_a_status_no_document_left_has_no_spread() -> None:
    reported = report(run(failed_extraction("b")))

    assert reported.status("validated").wait is None
    assert reported.status("validated").work is None


def test_every_pending_status_has_a_row_in_path_order() -> None:
    reported = report(run(approved("a", 0)))

    assert tuple(each.status for each in reported.statuses) == PENDING


def test_cost_per_document_sums_every_call_over_every_counted_document() -> None:
    reported = report(
        run(
            approved("a", 0, call("received", "0.003")),
            failed_extraction(
                "b", call("received", "0.001"), call("received", "0.002", attempt=2)
            ),
        )
    )

    assert reported.cost_per_document == Decimal("0.003")
    assert reported.status("received").cost_per_document == Decimal("0.003")
    assert reported.status("validated").cost_per_document == Decimal(0)


def test_a_case_the_loop_never_settled_is_refused() -> None:
    unsettled = SavedCase(
        document_id="c",
        id=3,
        status="extracted",
        routing_reasons=(),
        truth=(),
        transitions=(
            moved(None, "received", None, 0),
            moved("received", "extracted", 1, 2),
        ),
        vendor_calls=(),
    )

    with pytest.raises(LoopRunError, match="c never settled"):
        report(run(unsettled))


def test_a_loop_run_reads_back_as_written(tmp_path: Path) -> None:
    written = run(approved("a", 0, call("received", "0.003")), source="runs/gemini")

    write_loop_run(written, tmp_path)

    assert read_loop_run(tmp_path) == written


def test_a_directory_with_no_loop_run_says_which_command_writes_one(
    tmp_path: Path,
) -> None:
    with pytest.raises(LoopRunError, match="docmatch loop --out"):
        read_loop_run(tmp_path)
