"""Moving one document from status to status, and reading where it stands.

The path is `received -> extracted -> validated -> resolved -> matched ->
approved | needs_review`, with a failed extraction going from `received`
straight to `needs_review` (#150). Each middle status means the output that
reaches it is saved: the reading at `extracted`, the gate's result at
`validated` (whether or not it passed), resolution at `resolved`, the match
result at `matched`. So a document picked up again starts from its last
saved output and never reads the document twice.

A transition and its compare-and-set
------------------------------------

Moving a document on reads the output saved at its current status, computes
the next, and writes both the new status with its output and the transition
in one transaction under `WHERE status = <expected>` (ADR 0002). A second writer
from the same expected status finds the row moved and is refused, so a
document taken up twice never applies one transition twice.

The worker claims a document whose status is neither final nor
`needs_review`, one at a time, with `FOR UPDATE SKIP LOCKED` and a lease, and
moves it one status per claim. The claim's time is the transition's taken-up
time and the commit's the committed time, so the time since the previous
transition until the claim is queue wait and the time after it is work (#157).
The upload itself is the first transition, from no status to `received`, so
end-to-end latency starts where the upload was accepted.

A lease that lapses without a commit means the worker holding it stopped,
or raised and let the lease go at once; the next claim retakes the document
from its last saved status and counts the lapse against that status. The
claim that finds the third lapse is the one that routes the document.

Routing
-------

Decided once, at `matched`, with every reason attached, by `routing.route`
under P4: gate failed, match held (any hold finding). A gate that checked
nothing and a match whose only findings are notes never route. No reason
means `approved` with the system as actor. Two shortcuts route where the
document stands: a failed extraction, with extraction failed, and a document
whose lease lapsed a third time at one status, with pipeline failed naming
that status and every reason already known there (ADR 0002, #155).

Resolution
----------

At `validated` every line of the reading is resolved against the train
catalog through the resolver the worker was given, the hybrid at the
operating threshold, and saved at `resolved`: per line its top-1 SKU and
score, or no entry with its score, or null for a line with no description.
It annotates and never routes, and the match never reads it (#151).
"""

import json
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, TypeAdapter

from docmatch.extraction.derived import derived_header
from docmatch.extraction.extractor import (
    Confidence,
    Document,
    Extraction,
    Extractor,
)
from docmatch.extraction.pages import count, render
from docmatch.extraction.run import Attempt, read_document
from docmatch.gate import GateResult, gate
from docmatch.matching.matcher import MatchResult, explain, match
from docmatch.matching.records import read_record
from docmatch.metrics.fields import Prediction
from docmatch.pipeline.case import CASE, Case, case_json, digest
from docmatch.pipeline.labels import Labels
from docmatch.pipeline.routing import P4, RoutingReason, route, standing
from docmatch.pipeline.store import Connection
from docmatch.resolution.operating import Resolved, description_of

Status = Literal[
    "received",
    "extracted",
    "validated",
    "resolved",
    "matched",
    "needs_review",
    "approved",
    "rejected",
]

PENDING: tuple[Status, ...] = (
    "received",
    "extracted",
    "validated",
    "resolved",
    "matched",
)
"""The statuses the loop moves a document on from, in path order."""

SETTLED: tuple[Status, ...] = ("approved", "needs_review")
"""Where the loop leaves a document for good or for a reviewer."""

Decision = Literal["approved", "rejected"]
"""What a reviewer can move a document in review to, for good (#150)."""

PIPELINE_FAILED: dict[Status, RoutingReason] = {
    "received": "pipeline failed at received",
    "extracted": "pipeline failed at extracted",
    "validated": "pipeline failed at validated",
    "resolved": "pipeline failed at resolved",
    "matched": "pipeline failed at matched",
}
"""The pipeline-failed reason for each status a worker can claim at."""

LEASE = timedelta(minutes=10)
"""How long a claim holds a document before another worker may retake it.

Above the slowest bounded extraction: #35's three attempts, each ended by
its backend's 120 s timeout, plus the 2 s and 4 s backoffs come to 366 s.
Azure's timeout bounds the polling, not the analyze request before it, so
ten minutes leaves room for that, rendering and saving beside it: a lapse
means the worker stopped, never that a live reading was slow."""

LAPSES = 3
"""Lapsed leases at one status that route a document as pipeline failed:
the same three #35 gives a reading's attempts (ADR 0002)."""

GATE = TypeAdapter(GateResult)
MATCH = TypeAdapter(MatchResult)
RESOLUTION = TypeAdapter(list[Resolved | None])

Resolve = Callable[[str], Resolved]
"""A line's description to its resolution: `serve` gives the hybrid over the
catalog at the operating threshold, a test whatever it needs."""


class Reading(BaseModel):
    """A reading as the loop saves it at `extracted`: what the backend read,
    and what it returned beside it."""

    model_config = ConfigDict(frozen=True)

    prediction: Prediction
    currency_symbols: dict[str, tuple[str, ...]] = {}
    confidence: Confidence | None = None


class Refused(Exception):
    """A transition from a status the document no longer stands at."""


@dataclass(frozen=True)
class Upload:
    """An upload as the loop stored it."""

    document: int
    created: bool
    """False when the same content was already stored, and this is it."""


def upload(connection: Connection, invoice: bytes, case: Case) -> Upload:
    """Store an upload at `received` with its first transition, or return the
    document already stored under the same content's digest.

    The PDF's pages are counted first, so a file pdfium cannot open raises
    `PageError` before anything is stored."""
    pages = count(invoice)
    content = digest(invoice, case)
    with connection.transaction():
        row = connection.execute(
            """
            INSERT INTO documents (digest, invoice, pages, "case", status)
            VALUES (%s, %s, %s, %s, 'received')
            ON CONFLICT (digest) DO NOTHING
            RETURNING id
            """,
            (content, invoice, pages, Jsonb(case_json(case))),
        ).fetchone()
        if row is None:
            existing = connection.execute(
                "SELECT id FROM documents WHERE digest = %s", (content,)
            ).fetchone()
            assert existing is not None
            return Upload(_id(existing[0]), created=False)
        document = _id(row[0])
        connection.execute(
            """
            INSERT INTO transitions
                (document, from_status, to_status, committed_at, actor)
            VALUES (%s, NULL, 'received', clock_timestamp(), 'system')
            """,
            (document,),
        )
    return Upload(document, created=True)


@dataclass(frozen=True)
class Claim:
    """A document a worker has taken up, and when."""

    document: int
    status: Status
    taken_at: datetime
    lapses: int = 0
    """Leases that lapsed at this status before this claim."""


def claim(connection: Connection, lease: timedelta = LEASE) -> Claim | None:
    """The oldest pending document whose lease is free, leased to this worker,
    or None when nothing is pending. A lease still set is one that lapsed,
    since a commit lets its lease go, so it is counted against the status."""
    row = connection.execute(
        """
        UPDATE documents
        SET lease_until = clock_timestamp() + %s, taken_at = clock_timestamp(),
            lapses = CASE WHEN lease_until IS NULL THEN lapses
                ELSE lapses || jsonb_build_object(
                    status, coalesce((lapses ->> status)::int, 0) + 1)
                END
        WHERE id = (
            SELECT id FROM documents
            WHERE status NOT IN ('approved', 'rejected', 'needs_review')
              AND (lease_until IS NULL OR lease_until < clock_timestamp())
            ORDER BY id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, status, taken_at, (lapses ->> status)::int
        """,
        (lease,),
    ).fetchone()
    if row is None:
        return None
    return Claim(
        _id(row[0]),
        cast(Status, row[1]),
        cast(datetime, row[2]),
        cast(int, row[3] or 0),
    )


def advance(
    connection: Connection,
    taken: Claim,
    extractor: Extractor,
    resolve: Resolve,
    *,
    wait: Callable[[float], None] = time.sleep,
) -> Status:
    """One transition from the claimed status, written under compare-and-set; the
    status the document now stands at. Raises `Refused` when another writer
    moved it first."""
    if taken.lapses >= LAPSES:
        return _commit(
            connection,
            taken,
            "needs_review",
            reasons=(
                *_known(connection, taken.document),
                PIPELINE_FAILED[taken.status],
            ),
        )
    if taken.status == "received":
        return _extract(connection, taken, extractor, wait)
    if taken.status == "extracted":
        reading = _reading(connection, taken.document)
        checked = gate(derived_header(reading.prediction, reading.currency_symbols))
        return _commit(
            connection,
            taken,
            "validated",
            ("gate", GATE.dump_python(checked, mode="json")),
        )
    if taken.status == "validated":
        reading = _reading(connection, taken.document)
        resolution = [
            None
            if (description := description_of(row)) is None
            else resolve(description)
            for row in reading.prediction.rows
        ]
        return _commit(
            connection,
            taken,
            "resolved",
            ("resolution", RESOLUTION.dump_python(resolution, mode="json")),
        )
    if taken.status == "resolved":
        reading = _reading(connection, taken.document)
        case = _case(connection, taken.document)
        result = match(
            read_record(reading.prediction, reading.currency_symbols),
            case.purchase_order,
            case.receipt,
        )
        return _commit(
            connection,
            taken,
            "matched",
            ("match", MATCH.dump_python(result, mode="json")),
        )
    if taken.status == "matched":
        reasons = _known(connection, taken.document)
        if reasons:
            return _commit(connection, taken, "needs_review", reasons=reasons)
        return _commit(connection, taken, "approved")
    raise Refused(
        f"document {taken.document} is {taken.status}; the loop moves it no further"
    )


def claim_and_advance(
    connection: Connection,
    extractor: Extractor,
    resolve: Resolve,
    *,
    lease: timedelta = LEASE,
    wait: Callable[[float], None] = time.sleep,
) -> Status | None:
    """What the worker does each turn: claim a pending document and move it
    one status; None when nothing is pending. When moving it raises, the
    lease is let go at once, so the next claim counts the lapse and a
    document that fails the same way every time routes within the run
    instead of after three full leases."""
    taken = claim(connection, lease)
    if taken is None:
        return None
    try:
        return advance(connection, taken, extractor, resolve, wait=wait)
    except Refused:
        raise
    except Exception:
        _let_go(connection, taken)
        raise


def _let_go(connection: Connection, taken: Claim) -> None:
    """End this claim's lease now, leaving it set so the next claim counts
    it as lapsed; a lease another claim has taken since is left alone."""
    connection.execute(
        """
        UPDATE documents SET lease_until = clock_timestamp()
        WHERE id = %s AND taken_at = %s AND status = %s
        """,
        (taken.document, taken.taken_at, taken.status),
    )


def _known(connection: Connection, document: int) -> tuple[RoutingReason, ...]:
    """The reasons the outputs saved so far already give."""
    row = connection.execute(
        "SELECT gate, match FROM documents WHERE id = %s", (document,)
    ).fetchone()
    assert row is not None
    checked, matched = row
    reasons = route(
        standing(
            None if checked is None else GATE.validate_python(checked),
            None if matched is None else MATCH.validate_python(matched),
        ),
        P4,
    )
    # P4 has no confidence edge, so every reason it gives is the loop's own.
    return cast(tuple[RoutingReason, ...], reasons)


def _extract(
    connection: Connection,
    taken: Claim,
    extractor: Extractor,
    wait: Callable[[float], None],
) -> Status:
    """Read the PDF through the backend, each attempt kept as a vendor call as
    it returns, then save the reading or route the failure. The labels backend
    sends no request, so it has no vendor call to keep."""
    row = connection.execute(
        "SELECT invoice, pages FROM documents WHERE id = %s", (taken.document,)
    ).fetchone()
    assert row is not None
    invoice, pages = cast(bytes, row[0]), cast(int, row[1])

    def kept(attempt: Attempt) -> None:
        _vendor_call(connection, taken.document, taken.status, attempt)

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "invoice.pdf"
        path.write_bytes(invoice)
        read = read_document(
            extractor,
            Document(str(taken.document), path, pages),
            wait=wait,
            attempted=(lambda _: None) if isinstance(extractor, Labels) else kept,
        )
    if read.prediction is None:
        return _commit(
            connection, taken, "needs_review", reasons=("extraction failed",)
        )
    reading = Reading(
        prediction=read.prediction,
        currency_symbols={
            source: tuple(symbols) for source, symbols in read.currency_symbols.items()
        },
        confidence=read.confidence,
    )
    return _commit(
        connection, taken, "extracted", ("reading", reading.model_dump(mode="json"))
    )


def _vendor_call(
    connection: Connection, document: int, status: Status, attempt: Attempt
) -> None:
    """One attempt, committed on its own the moment it returned."""
    outcome = attempt.outcome
    usage = outcome.usage
    read = outcome if isinstance(outcome, Extraction) else None
    connection.execute(
        """
        INSERT INTO vendor_calls (document, status, attempt, units, cost,
            served_model, started_at, ended_at, response, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            document,
            status,
            attempt.number,
            Jsonb(
                {
                    "input_tokens": usage.input_tokens,
                    "cached_input_tokens": usage.cached_input_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "output_tokens": usage.output_tokens,
                    "pages": usage.pages,
                }
            ),
            outcome.cost,
            None if read is None else read.served_model,
            attempt.started,
            attempt.ended,
            None if read is None else _reading_json(read.prediction),
            str(outcome) if read is None else None,
        ),
    )


def _reading_json(prediction: Prediction) -> str:
    """A reading as a vendor call keeps it for its response."""
    return json.dumps(prediction.model_dump(exclude_defaults=True), ensure_ascii=False)


Output = tuple[Literal["reading", "gate", "resolution", "match"], object]
"""The checkpoint column a transition saves, and what it saves there."""


def _commit(
    connection: Connection,
    taken: Claim,
    to: Status,
    output: Output | None = None,
    *,
    reasons: Sequence[RoutingReason] = (),
) -> Status:
    """The new status, its output and its transition in one transaction, only
    if the document still stands where it was claimed; the lease is let go."""
    assignments: list[sql.Composable] = [
        sql.SQL("status = %s, lease_until = NULL, taken_at = NULL")
    ]
    values: list[object] = [to]
    if output is not None:
        column, value = output
        assignments.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
        values.append(Jsonb(value))
    with connection.transaction():
        moved = connection.execute(
            sql.SQL("UPDATE documents SET {} WHERE id = %s AND status = %s").format(
                sql.SQL(", ").join(assignments)
            ),
            (*values, taken.document, taken.status),
        ).rowcount
        if moved != 1:
            raise Refused(
                f"document {taken.document} no longer stands at {taken.status}"
            )
        connection.execute(
            """
            INSERT INTO transitions
                (document, from_status, to_status, taken_at, committed_at,
                 actor, reasons)
            VALUES (%s, %s, %s, %s, clock_timestamp(), 'system', %s)
            """,
            (taken.document, taken.status, to, taken.taken_at, list(reasons)),
        )
    return to


def _column(connection: Connection, document: int, column: str) -> object:
    row = connection.execute(
        sql.SQL("SELECT {} FROM documents WHERE id = %s").format(
            sql.Identifier(column)
        ),
        (document,),
    ).fetchone()
    assert row is not None
    return row[0]


def _reading(connection: Connection, document: int) -> Reading:
    return Reading.model_validate(_column(connection, document, "reading"))


def _case(connection: Connection, document: int) -> Case:
    return CASE.validate_python(_column(connection, document, "case"))


def _id(value: object) -> int:
    assert isinstance(value, int)
    return value


def decide(connection: Connection, document: int, to: Decision) -> None:
    """A reviewer's decision, final: the document moves from `needs_review`
    under compare-and-set with its transition, actor `reviewer` and no take
    time, since only a change the system makes is taken up (#157). Raises
    `Refused` when the document does not stand in review."""
    with connection.transaction():
        moved = connection.execute(
            """
            UPDATE documents SET status = %s
            WHERE id = %s AND status = 'needs_review'
            """,
            (to, document),
        ).rowcount
        if moved != 1:
            raise Refused(f"document {document} is not in review")
        connection.execute(
            """
            INSERT INTO transitions
                (document, from_status, to_status, committed_at, actor)
            VALUES (%s, 'needs_review', %s, clock_timestamp(), 'reviewer')
            """,
            (document, to),
        )


def queue(connection: Connection, status: Status) -> list[dict[str, object]]:
    """The documents at one status, oldest first, each with its routing
    reasons: what the review page's strip lists."""
    ids = [
        _id(row[0])
        for row in connection.execute(
            "SELECT id FROM documents WHERE status = %s ORDER BY id", (status,)
        ).fetchall()
    ]
    return [
        {"id": each, "status": status, "routing_reasons": _reasons(connection, each)}
        for each in ids
    ]


def page(connection: Connection, document: int, number: int) -> bytes | None:
    """One page of the uploaded PDF as a PNG, rendered the way a vision
    backend is sent it; None when there is no such document or page."""
    row = connection.execute(
        "SELECT invoice, pages FROM documents WHERE id = %s", (document,)
    ).fetchone()
    if row is None or not 1 <= number <= cast(int, row[1]):
        return None
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "invoice.pdf"
        path.write_bytes(cast(bytes, row[0]))
        return render(path)[number - 1].png


def view(connection: Connection, document: int) -> dict[str, object] | None:
    """One document as the API shows it: status, pages, case, reading, gate,
    resolution per line, match result with each finding explained, routing
    reasons, cost and latency; None when there is no such document. An
    output not yet saved is null."""
    row = connection.execute(
        """
        SELECT status, pages, "case", reading, gate, resolution, match
        FROM documents WHERE id = %s
        """,
        (document,),
    ).fetchone()
    if row is None:
        return None
    status, pages, case, reading, checked, resolution, matched = row
    return {
        "id": document,
        "status": status,
        "pages": pages,
        "case": case,
        "reading": reading,
        "gate": None if checked is None else _gate_view(GATE.validate_python(checked)),
        "resolution": resolution,
        "match": None
        if matched is None
        else _match_view(MATCH.validate_python(matched)),
        "routing_reasons": _reasons(connection, document),
        "cost": str(cost(connection, document)),
        "latency": latency(connection, document),
    }


def _gate_view(checked: GateResult) -> dict[str, object]:
    dumped = cast(dict[str, object], GATE.dump_python(checked, mode="json"))
    return {"verdict": checked.verdict, **dumped}


def _match_view(result: MatchResult) -> dict[str, object]:
    dumped = cast(dict[str, object], MATCH.dump_python(result, mode="json"))
    findings = cast(list[dict[str, object]], dumped["findings"])
    return {
        "verdict": result.verdict,
        "pairings": dumped["pairings"],
        "findings": [
            {**each, "severity": finding.severity, "explanation": explain(finding)}
            for each, finding in zip(findings, result.findings, strict=True)
        ],
        "not_compared": dumped["not_compared"],
    }


def _reasons(connection: Connection, document: int) -> list[str]:
    """The reasons on the transition that sent the document to review, or
    none when it has not gone there."""
    row = connection.execute(
        """
        SELECT reasons FROM transitions
        WHERE document = %s AND to_status = 'needs_review'
        ORDER BY id DESC LIMIT 1
        """,
        (document,),
    ).fetchone()
    return [] if row is None else cast(list[str], row[0])


def cost(connection: Connection, document: int) -> Decimal:
    """Cost per document: the sum of its vendor calls, every attempt counted."""
    row = connection.execute(
        "SELECT coalesce(sum(cost), 0) FROM vendor_calls WHERE document = %s",
        (document,),
    ).fetchone()
    assert row is not None
    return cast(Decimal, row[0])


def latency(connection: Connection, document: int) -> float | None:
    """Seconds from the upload accepted to the loop settling the document at
    `approved` or `needs_review`, queue wait included; None until it has. The
    pipeline report reads the same span off a saved loop run's transitions."""
    row = connection.execute(
        """
        SELECT extract(epoch FROM settled.committed_at - uploaded.committed_at)
        FROM transitions uploaded, transitions settled
        WHERE uploaded.document = %s AND uploaded.from_status IS NULL
          AND settled.document = %s AND settled.actor = 'system'
          AND settled.to_status IN ('approved', 'needs_review')
        ORDER BY settled.id LIMIT 1
        """,
        (document, document),
    ).fetchone()
    return None if row is None else float(cast(Decimal, row[0]))


def trace(connection: Connection, document: int) -> dict[str, object] | None:
    """A document's transitions and vendor calls, without what came back;
    None when there is no such document."""
    if (
        connection.execute(
            "SELECT 1 FROM documents WHERE id = %s", (document,)
        ).fetchone()
        is None
    ):
        return None
    transitions = connection.execute(
        """
        SELECT from_status, to_status, taken_at, committed_at, actor, reasons
        FROM transitions WHERE document = %s ORDER BY id
        """,
        (document,),
    ).fetchall()
    calls = connection.execute(
        """
        SELECT status, attempt, units, cost, served_model, started_at, ended_at,
            error IS NOT NULL
        FROM vendor_calls WHERE document = %s ORDER BY id
        """,
        (document,),
    ).fetchall()
    return {
        "id": document,
        "transitions": [
            {
                "from": each[0],
                "to": each[1],
                "taken_at": _time(each[2]),
                "committed_at": _time(each[3]),
                "actor": each[4],
                "reasons": each[5],
            }
            for each in transitions
        ],
        "vendor_calls": [
            {
                "status": each[0],
                "attempt": each[1],
                "units": each[2],
                "cost": str(each[3]),
                "served_model": each[4],
                "started_at": _time(each[5]),
                "ended_at": _time(each[6]),
                "failed": each[7],
            }
            for each in calls
        ],
    }


def _time(value: object) -> str | None:
    return None if value is None else cast(datetime, value).isoformat()
