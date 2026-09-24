"""`docmatch loop`: one measurement of the loop, saved as a loop run.

It draws one case per fixed-subset document with labeled lines under the
generator's pinned seed, half clean and half with one injected discrepancy
(#152), makes a Postgres schema no run has used, and starts `docmatch serve`
on it as a subprocess with the backend asked for, so a run is one command and
can never talk to a server reading with another backend (#164). Each case is
uploaded over HTTP one at a time, so queue wait measures pickup and not a
backlog, and polled with `GET /documents/{id}` until the loop settles it at
`approved` or `needs_review`. Its trace is read back through the API, the
server is stopped, and the loop run is written to `--out`. The schema is kept,
for a reviewer to work the run's documents afterwards.

Polling does not move latency: the report reads it from the transitions'
own times (#157). The client is httpx 0.28.1, the version `uv.lock` pins,
with `files=` for the PDF part and `data=` for the case part, the way the
API's tests already upload; its signatures read from the installed package
2026-09-24. `trust_env` is off so no proxy setting reaches a loopback call.

The server's own output, uvicorn's request log and any worker error, goes to
`serve.log` beside the run, which is where to look when a run stops early.
A run interrupted before its last case saves nothing: its schema stays, and
the next run starts a new one.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

import httpx

from docmatch.docile.dataset import DocileDataset
from docmatch.evals import public
from docmatch.evals.confidence import gated_confidence
from docmatch.evals.manifest import Manifest
from docmatch.extraction.derived import derived_header
from docmatch.matching import generator
from docmatch.matching.generator import one_per_document
from docmatch.matching.pool import pool_from
from docmatch.metrics.fields import FieldValues, labeled_fields
from docmatch.pipeline import replay
from docmatch.pipeline.case import Case, case_json
from docmatch.pipeline.ladder import misread
from docmatch.pipeline.loop import GATE, LAPSES, LEASE, MATCH, SETTLED, Reading
from docmatch.pipeline.routing import standing
from docmatch.pipeline.saved import LoopRun, SavedCase, write_loop_run
from docmatch.pipeline.serve import HOST
from docmatch.resolution.store import DATABASE_URL_VARIABLE, connect

LOG_FILE = "serve.log"

STARTUP = 120.0
"""Seconds the server has to answer its first request: room for the embedder
loaded and the catalog embedded at startup, which a server that has stopped
never needs, since its exit is noticed at once."""

SETTLE = (LAPSES + 1) * LEASE.total_seconds()
"""Seconds one case may take to settle: every lease a document can lapse
before it routes as pipeline failed, and one more for the claim that routes
it. Past that the worker is not moving it at all."""

POLL = 0.05
"""Seconds between two reads of a document's status."""

STOP = 30.0
"""Seconds the server has to stop after Ctrl-C before it is killed."""


class LoopError(Exception):
    """The loop could not be run to the end."""


RUNS = Path("data/runs")
"""Where a loop run is saved without `--out`: ignored by git, beside the
extraction runs, in a directory named for its schema."""


def measure(
    *,
    backend: str,
    requested_model: str,
    source: Path | None,
    dataset: DocileDataset,
    pinned: Manifest,
    manifest_path: Path,
    copies: Path,
    database_url: str,
    out: Path | None = None,
) -> tuple[LoopRun, Path]:
    """Run every case through a fresh server and save the loop run in `out`,
    or under `RUNS` in a directory named for its schema; the run and where
    it went.

    The public copies are verified before anything starts, so a missing or
    changed PDF ends the run before a schema is made."""
    public.verify(pinned, copies)
    pool = pool_from(
        "fixed subset",
        (
            (document_id, dataset.annotation(document_id))
            for document_id in pinned.document_ids
        ),
    )
    with_lines = {each.document_id for each in pool.seeds}
    cases = one_per_document(pool.seeds)
    schema = _new_schema(database_url, backend)
    out = RUNS / schema if out is None else out
    out.mkdir(parents=True, exist_ok=True)
    with _served(
        backend, source, dataset.root, schema, database_url, out / LOG_FILE
    ) as server:
        saved = [
            server.run(
                each, copies, labeled_fields(dataset.annotation(each.document_id))
            )
            for each in cases
        ]
    run = LoopRun(
        backend=backend,
        source=None if source is None else str(source),
        requested_model=requested_model,
        database_schema=schema,
        seed=generator.SEED,
        manifest=str(manifest_path),
        documents=pinned.size,
        without_lines=tuple(
            each for each in pinned.document_ids if each not in with_lines
        ),
        cases=tuple(saved),
    )
    write_loop_run(run, out)
    return run, out


def _new_schema(database_url: str, backend: str) -> str:
    """A schema name no run has used, named for the backend and the time."""
    name = f"loop_{backend}_{datetime.now(UTC):%Y%m%d_%H%M%S}"
    with connect(database_url) as connection:
        found = connection.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            (name,),
        ).fetchone()
        if found is not None:
            raise LoopError(
                f"schema {name} is already there; a loop run starts on a schema "
                "of its own"
            )
    return name


def _free_port() -> int:
    """A port nothing listens on now, so a server already running is never the
    one the run talks to."""
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return cast(int, probe.getsockname()[1])


@dataclass
class _Server:
    """The subprocess serving the run, and a client talking to it."""

    process: subprocess.Popen[bytes]
    client: httpx.Client
    log: Path

    def alive(self) -> None:
        """Raise when the server has stopped."""
        code = self.process.poll()
        if code is not None:
            raise LoopError(
                f"`docmatch serve` stopped with exit code {code}; its output is "
                f"in {self.log}"
            )

    def run(
        self, case: generator.Case, copies: Path, labeled: FieldValues
    ) -> SavedCase:
        """One case uploaded, waited on until it settles, and its trace read;
        what the ladder reads worked out against the document's labels."""
        invoice = public.path(copies, case.document_id).read_bytes()
        body = json.dumps(case_json(Case(case.purchase_order, case.receipt)))
        uploaded = self._request(
            "POST",
            "/documents",
            files={"invoice": (f"{case.document_id}.pdf", invoice, "application/pdf")},
            data={"case": body},
        )
        if uploaded.status_code != httpx.codes.CREATED:
            raise LoopError(
                f"uploading {case.document_id} answered {uploaded.status_code}: "
                f"{uploaded.text}"
            )
        document = cast(int, uploaded.json()["id"])
        view = self._settled(case.document_id, document)
        trace = self._get(f"/documents/{document}/trace")
        reading = cast(dict[str, object] | None, view["reading"])
        return SavedCase.model_validate(
            {
                "document_id": case.document_id,
                "id": document,
                "status": view["status"],
                "routing_reasons": view["routing_reasons"],
                "truth": case.truth,
                "transitions": trace["transitions"],
                "vendor_calls": trace["vendor_calls"],
                "confidence": None if reading is None else reading["confidence"],
                **_for_the_ladder(view, labeled),
            }
        )

    def _settled(self, document_id: str, document: int) -> dict[str, object]:
        deadline = time.monotonic() + SETTLE
        while True:
            view = self._get(f"/documents/{document}")
            if view["status"] in SETTLED:
                return view
            self.alive()
            if time.monotonic() > deadline:
                raise LoopError(
                    f"{document_id} is still {view['status']} after {SETTLE:.0f} s; "
                    f"the worker is not moving it, see {self.log}"
                )
            time.sleep(POLL)

    def _get(self, path: str) -> dict[str, object]:
        answered = self._request("GET", path)
        if answered.status_code != httpx.codes.OK:
            raise LoopError(f"GET {path} answered {answered.status_code}")
        return cast(dict[str, object], answered.json())

    def _request(self, method: str, path: str, **sent: Any) -> httpx.Response:
        """The server's answer, or a message pointing at its log when it gave
        none: stopped mid-request, or past the client's timeout."""
        try:
            return self.client.request(method, path, **sent)
        except httpx.TransportError as error:
            raise LoopError(
                f"{method} {path} got no answer from `docmatch serve` ({error!r}); "
                f"its output is in {self.log}"
            ) from error


def _for_the_ladder(view: dict[str, object], labeled: FieldValues) -> dict[str, object]:
    """What routing and escape counting read of a settled document, worked out
    from its view and its labels, keeping no value the document says."""
    reading = (
        None if view["reading"] is None else Reading.model_validate(view["reading"])
    )
    checked = None if view["gate"] is None else GATE.validate_python(view["gate"])
    result = None if view["match"] is None else MATCH.validate_python(view["match"])
    known = standing(checked, result)
    confident = (
        ()
        if reading is None or reading.confidence is None or checked is None
        else gated_confidence(reading.prediction.fields, reading.confidence, checked)
    )
    return {
        "gate": known.gate,
        "holds": known.holds,
        "gated_confidence": confident,
        "misread": ()
        if reading is None
        else misread(
            derived_header(reading.prediction, reading.currency_symbols), labeled
        ),
    }


@contextmanager
def _served(
    backend: str,
    source: Path | None,
    data_dir: Path,
    schema: str,
    database_url: str,
    log: Path,
) -> Iterator[_Server]:
    """`docmatch serve` on the schema until the block ends, then stopped."""
    port = _free_port()
    command: Sequence[str] = [
        sys.executable,
        "-m",
        "docmatch",
        "serve",
        "--backend",
        backend,
        *(("--run", str(source)) if backend == replay.BACKEND and source else ()),
        "--data-dir",
        str(data_dir),
        "--schema",
        schema,
        "--port",
        str(port),
    ]
    environment = {**os.environ, DATABASE_URL_VARIABLE: database_url}
    with (
        log.open("wb") as written,
        httpx.Client(
            base_url=f"http://{HOST}:{port}", timeout=30.0, trust_env=False
        ) as client,
    ):
        process = subprocess.Popen(
            command, stdout=written, stderr=subprocess.STDOUT, env=environment
        )
        server = _Server(process, client, log)
        try:
            _wait_for(server)
            yield server
        finally:
            _stop(process, cast(IO[bytes], written))


def _wait_for(server: _Server) -> None:
    """Return once the server answers a request."""
    deadline = time.monotonic() + STARTUP
    while True:
        server.alive()
        try:
            server.client.get("/documents/0")
            return
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise LoopError(
                    f"`docmatch serve` did not answer in {STARTUP:.0f} s; its "
                    f"output is in {server.log}"
                ) from None
            time.sleep(POLL)


def _stop(process: subprocess.Popen[bytes], log: IO[bytes]) -> None:
    """Ctrl-C, as a person would stop it, then a kill if it does not stop."""
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(STOP)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    log.flush()
