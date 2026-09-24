"""`docmatch serve`: the API and the worker over one loop run's schema.

One process: uvicorn 0.53.0 serves the API on 127.0.0.1 (`uvicorn.run`,
signature read from the installed package 2026-09-24) while one worker thread
claims and advances pending documents on its own connection, one status per
claim. When nothing is pending the worker waits a moment and asks again. When
uvicorn stops, on Ctrl-C or a signal, the worker is told to stop and joined.

A transition that fails is reported and left: the document keeps its lease
until the lease lapses and a later claim retakes it from its last checkpoint.
"""

import sys
import threading
from datetime import timedelta

import uvicorn

from docmatch.extraction.extractor import Extractor
from docmatch.pipeline import api
from docmatch.pipeline.loop import LEASE, Refused, claim_and_advance
from docmatch.pipeline.store import open_schema, prepare

HOST = "127.0.0.1"
PORT = 8000

IDLE = 0.2
"""Seconds the worker waits before asking again when nothing is pending. It
is pickup delay, and it shows in the queue wait a transition records."""


def serve(
    database_url: str, schema: str, extractor: Extractor, *, port: int = PORT
) -> None:
    """Prepare the schema, start the worker, and serve until stopped."""
    prepare(database_url, schema)
    stopped = threading.Event()
    worker = threading.Thread(
        target=work,
        args=(database_url, schema, extractor, stopped),
        name="docmatch-worker",
    )
    worker.start()
    try:
        uvicorn.run(api.create(database_url, schema), host=HOST, port=port)
    finally:
        stopped.set()
        worker.join()


def work(
    database_url: str,
    schema: str,
    extractor: Extractor,
    stopped: threading.Event,
    *,
    lease: timedelta = LEASE,
) -> None:
    """Claim and advance until told to stop."""
    with open_schema(database_url, schema) as connection:
        while not stopped.is_set():
            try:
                moved = claim_and_advance(connection, extractor, lease=lease)
            except Refused as refused:
                print(f"docmatch worker: {refused}", file=sys.stderr)
                continue
            except Exception as error:  # one document's failure, not the server's
                print(f"docmatch worker: {error!r}", file=sys.stderr)
                moved = None
            if moved is None:
                stopped.wait(IDLE)
