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

Routing
-------

Decided once, at `matched`, with every reason attached: gate failed, match
held (any hold finding). A gate that checked nothing and a match whose only
findings are notes never route. No reason means `approved` with the system as
actor. A failed extraction routes where it stands, with extraction failed.

Resolution is a pass-through checkpoint for now: `resolved` saves no output
and the match never reads one (#170 fills it in).
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
from docmatch.extraction.pages import count
from docmatch.extraction.run import Attempt, read_document
from docmatch.gate import GateResult, gate
from docmatch.matching.matcher import MatchResult, explain, match
from docmatch.matching.records import read_record
from docmatch.metrics.fields import Prediction
from docmatch.pipeline.case import CASE, Case, case_json, digest
from docmatch.pipeline.store import Connection

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

RoutingReason = Literal["extraction failed", "gate failed", "match held"]

LEASE = timedelta(minutes=5)
"""How long a claim holds a document before another worker may retake it.

Above the slowest bounded extraction: three attempts of Phase 1's slowest row
(OpenAI, p95 38.6 s) plus the 2 s and 4 s backoffs come to about two minutes,
so five leaves room for a slow tail without a lapse retaking live work."""

GATE = TypeAdapter(GateResult)
MATCH = TypeAdapter(MatchResult)


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


def claim(connection: Connection, lease: timedelta = LEASE) -> Claim | None:
    """The oldest pending document whose lease is free, leased to this worker,
    or None when nothing is pending."""
    row = connection.execute(
        """
        UPDATE documents
        SET lease_until = clock_timestamp() + %s, taken_at = clock_timestamp()
        WHERE id = (
            SELECT id FROM documents
            WHERE status NOT IN ('approved', 'rejected', 'needs_review')
              AND (lease_until IS NULL OR lease_until < clock_timestamp())
            ORDER BY id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, status, taken_at
        """,
        (lease,),
    ).fetchone()
    if row is None:
        return None
    return Claim(_id(row[0]), cast(Status, row[1]), cast(datetime, row[2]))


def advance(
    connection: Connection,
    taken: Claim,
    extractor: Extractor,
    *,
    wait: Callable[[float], None] = time.sleep,
) -> Status:
    """One transition from the claimed status, written under compare-and-set; the
    status the document now stands at. Raises `Refused` when another writer
    moved it first."""
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
        return _commit(connection, taken, "resolved")
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
        checked, result = (
            _gate(connection, taken.document),
            _match(connection, taken.document),
        )
        reasons = route(checked, result)
        if reasons:
            return _commit(connection, taken, "needs_review", reasons=reasons)
        return _commit(connection, taken, "approved")
    raise Refused(
        f"document {taken.document} is {taken.status}; the loop moves it no further"
    )


def claim_and_advance(
    connection: Connection,
    extractor: Extractor,
    *,
    lease: timedelta = LEASE,
    wait: Callable[[float], None] = time.sleep,
) -> Status | None:
    """What the worker does each turn: claim a pending document and move it
    one status; None when nothing is pending."""
    taken = claim(connection, lease)
    if taken is None:
        return None
    return advance(connection, taken, extractor, wait=wait)


def route(checked: GateResult, result: MatchResult) -> tuple[RoutingReason, ...]:
    """Every reason a matched document goes to review, in the order a
    reviewer reads them; none means the system approves it."""
    reasons: list[RoutingReason] = []
    if checked.verdict == "failed":
        reasons.append("gate failed")
    if result.verdict == "held":
        reasons.append("match held")
    return tuple(reasons)


def _extract(
    connection: Connection,
    taken: Claim,
    extractor: Extractor,
    wait: Callable[[float], None],
) -> Status:
    """Read the PDF through the backend, each attempt kept as a vendor call as
    it returns, then save the reading or route the failure."""
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
            attempted=kept,
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


Output = tuple[Literal["reading", "gate", "match"], object]
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


def _gate(connection: Connection, document: int) -> GateResult:
    return GATE.validate_python(_column(connection, document, "gate"))


def _match(connection: Connection, document: int) -> MatchResult:
    return MATCH.validate_python(_column(connection, document, "match"))


def _id(value: object) -> int:
    assert isinstance(value, int)
    return value


def view(connection: Connection, document: int) -> dict[str, object] | None:
    """One document as the API shows it: status, case, reading, gate, match
    result with each finding explained, routing reasons, cost and latency;
    None when there is no such document. An output not yet saved is null."""
    row = connection.execute(
        """
        SELECT status, "case", reading, gate, match FROM documents WHERE id = %s
        """,
        (document,),
    ).fetchone()
    if row is None:
        return None
    status, case, reading, checked, matched = row
    return {
        "id": document,
        "status": status,
        "case": case,
        "reading": reading,
        "gate": None if checked is None else _gate_view(GATE.validate_python(checked)),
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
    `approved` or `needs_review`, queue wait included; None until it has."""
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
