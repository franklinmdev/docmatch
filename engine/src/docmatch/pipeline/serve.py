"""`docmatch serve`: the API and the worker over one loop run's schema.

Before anything is served, the embedder is loaded once and the train catalog
is rebuilt beside the loop's schema, in a schema of its own named after it,
since a rebuild drops the schema it builds in (`resolution.store`). A server
restarted on a kept schema rebuilds the same entries under the same SKUs.
The worker resolves every line through the hybrid at the operating threshold
over that catalog, on its own connection (#151), and the API resolves a
reviewer's edit the same way on another (#155 point 4).

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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import uvicorn

from docmatch.extraction.extractor import Extractor
from docmatch.pipeline import api
from docmatch.pipeline.loop import LEASE, Refused, Resolve, claim_and_advance
from docmatch.pipeline.store import open_schema, prepare
from docmatch.resolution.catalog import Catalog
from docmatch.resolution.models import Embedder, ModelLoader
from docmatch.resolution.operating import Resolved, resolve_line
from docmatch.resolution.store import Store, connect

HOST = "127.0.0.1"
PORT = 8000

IDLE = 0.2
"""Seconds the worker waits before asking again when nothing is pending. It
is pickup delay, and it shows in the queue wait a transition records."""


def catalog_schema(schema: str) -> str:
    """Where the catalog a loop run's lines resolve against is built."""
    return f"{schema}_catalog"


@contextmanager
def resolving(database_url: str, schema: str, embedder: Embedder) -> Iterator[Resolve]:
    """The hybrid at the operating threshold over the loop run's catalog. It
    reads on a connection of its own, since the loop's has its schema alone
    on the search path and the extensions' operators live outside it."""
    with connect(database_url) as connection:
        connection.autocommit = True
        store = Store(connection, catalog_schema(schema))

        def resolve(description: str) -> Resolved:
            return resolve_line(store, embedder, description)

        yield resolve


def serve(
    database_url: str,
    schema: str,
    backend: str,
    extractor: Extractor,
    catalog: Catalog,
    load: ModelLoader,
    *,
    port: int = PORT,
) -> None:
    """Prepare the schema, load the embedder, rebuild the catalog, start the
    worker, and serve until stopped."""
    prepare(database_url, schema)
    embedder = load().embedder
    with connect(database_url) as connection:
        Store(connection, catalog_schema(schema)).rebuild(catalog.entries, embedder)
    stopped = threading.Event()
    worker = threading.Thread(
        target=work,
        args=(database_url, schema, extractor, embedder, stopped),
        name="docmatch-worker",
    )
    worker.start()
    try:
        with resolving(database_url, schema, embedder) as resolve:
            uvicorn.run(
                api.create(database_url, schema, backend, resolve), host=HOST, port=port
            )
    finally:
        stopped.set()
        worker.join()


def work(
    database_url: str,
    schema: str,
    extractor: Extractor,
    embedder: Embedder,
    stopped: threading.Event,
    *,
    lease: timedelta = LEASE,
) -> None:
    """Claim and advance until told to stop."""
    with (
        open_schema(database_url, schema) as connection,
        resolving(database_url, schema, embedder) as resolve,
    ):
        while not stopped.is_set():
            try:
                moved = claim_and_advance(connection, extractor, resolve, lease=lease)
            except Refused as refused:
                print(f"docmatch worker: {refused}", file=sys.stderr)
                continue
            except Exception as error:  # one document's failure, not the server's
                print(f"docmatch worker: {error!r}", file=sys.stderr)
                moved = None
            if moved is None:
                stopped.wait(IDLE)
