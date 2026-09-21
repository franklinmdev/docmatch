"""Postgres as the working space the arms run over.

Every run drops and recreates schema `resolution`, creates one table of SKU,
canonical description and a 384-dimension vector column, embeds every
canonical description through the embedder, inserts the entries, builds the
GiST trigram index and the HNSW index after the insert, and leaves the schema
in place afterwards for inspection (#121). Nothing is cached: about 27 s of
rebuild against about 13 min of measurement, and a run at the linked commit
then reproduces the row, catalog included. What embedding the catalog and
building the HNSW index cost is timed and reported once, outside any query's
latency.

The HNSW index is `vector_cosine_ops` at m 16 and ef_construction 64, the
defaults, and the connection's `hnsw.ef_search` is set to 100 once the index
exists: at the default 40 the index disagreed with an exact scan on the
top-5 set for 3.3 percent of queries, at 100 for 1.2, at 200 for none, for
0.2 ms against 12 ms of embedding (#120, probe 2). pgvector README read
2026-09-21 (github.com/pgvector/pgvector, 0.8.6): `WITH (m, ef_construction)`
on the index, `SET hnsw.ef_search` on the session, a vector written as its
bracketed literal, and an index built after the data loads is faster.

Both extensions are required to exist, `vector` for the column type and
`pg_trgm` for the index, and a missing one is reported without a traceback.
Creating them is the cluster's business, not this module's: locally #115 did
it once by hand, and CI's step does it against the container.

Driver
------

psycopg 3.3.6, docs read 2026-09-21 (`docs/basic/usage.html`, `api/errors.html`
at psycopg.org/psycopg3), under #120's constraints: one connection, one
query at a time, parameters bound with `%s` placeholders so a description is
never spliced into SQL, and distances returned to code where the ordering is
applied. `psycopg.connect` takes the libpq URI as given, so `--database-url`
reaches the server unchanged, and it raises `OperationalError` when no server
answers. The `binary` extra ships libpq, so a fresh machine needs no client
library installed. Connection strings carry the password in the URI, so an
error names the connection without it.

Connection precedence, pinned in that order (#121): `--database-url`, then
`DOCMATCH_DATABASE_URL`, then `postgresql:///docmatch`, the socket the local
cluster already offers, so the command needs no flag on the named machine.
"""

import os
import platform
import time
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from docmatch.resolution.catalog import Entry, ResolutionError
from docmatch.resolution.models import Embedder, Vector

DATABASE_URL_VARIABLE = "DOCMATCH_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql:///docmatch"

SCHEMA = "resolution"
"""The schema every run rebuilds; tests build their catalogs in another."""

DIMENSIONS = 384
"""The embedder's output width, `all-MiniLM-L6-v2` (#112)."""

EXTENSIONS = ("vector", "pg_trgm")
"""Both required to exist before a run."""

HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
"""The HNSW graph's connections per layer and build candidate list, pgvector's
defaults, written out so a row can name them (#120)."""

EF_SEARCH = 100
"""The HNSW search candidate list, set on the connection (#120, probe 2)."""


class StoreError(ResolutionError):
    """A database that does not answer, or one missing an extension."""


def resolve_database_url(given: str | None) -> str:
    """Where the database is: the flag, then the environment, then the default."""
    if given is not None:
        return given
    from_environment = os.environ.get(DATABASE_URL_VARIABLE)
    return from_environment if from_environment else DEFAULT_DATABASE_URL


def connect(url: str) -> psycopg.Connection[tuple[object, ...]]:
    """One connection, or a `StoreError` naming the connection without its
    password when no database answers."""
    try:
        return psycopg.connect(url)
    except psycopg.OperationalError as error:
        raise StoreError(
            f"no database answers at {without_password(url)}: {error}"
        ) from None
    except psycopg.ProgrammingError as error:
        raise StoreError(f"not a database url: {error}") from None


def without_password(url: str) -> str:
    """The connection as libpq spells it, its password left out."""
    try:
        parts = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return "an unreadable database url"
    kept = {
        key: str(value)
        for key, value in parts.items()
        if key != "password" and value is not None
    }
    return make_conninfo("", **kept)


@dataclass(frozen=True)
class ServerVersions:
    """What the server reported at run time, so a local row and a CI row are
    never confused."""

    postgres: str
    pgvector: str
    pg_trgm: str


@dataclass(frozen=True)
class Machine:
    """What the OS reported at run time: latency is a property of a machine."""

    cpu: str
    logical_cpus: int
    memory_gib: float
    kernel: str


@dataclass(frozen=True)
class Build:
    """What building the catalog cost once, outside any query's latency."""

    embedding_s: float
    """Embedding every canonical description."""
    index_s: float
    """Building the HNSW index after the insert."""


@dataclass(frozen=True)
class Store:
    """The catalog in one schema of one database, over one connection."""

    connection: psycopg.Connection[tuple[object, ...]]
    schema: str = SCHEMA

    def rebuild(self, entries: Sequence[Entry], embedder: Embedder) -> Build:
        """Drop whatever the last run left, and build the catalog afresh.

        The extensions are checked first, so a missing one is reported as
        such rather than as a type or an operator class that does not exist.
        The descriptions are embedded before the transaction opens, so the
        old schema stays in place while the embedder works.
        """
        self.versions()
        started = time.perf_counter()
        vectors = embedder.embed([each.description for each in entries])
        embedding_s = time.perf_counter() - started
        schema = sql.Identifier(self.schema)
        table = sql.Identifier(self.schema, "catalog")
        with self.connection.transaction():
            self.connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(schema)
            )
            self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
            self.connection.execute(
                sql.SQL(
                    "CREATE TABLE {} ("
                    "sku text PRIMARY KEY, "
                    "description text NOT NULL, "
                    "embedding vector({}) NOT NULL)"
                ).format(table, sql.Literal(DIMENSIONS))
            )
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    sql.SQL(
                        "INSERT INTO {} (sku, description, embedding) "
                        "VALUES (%s, %s, %s)"
                    ).format(table),
                    [
                        (each.sku, each.description, vector_literal(vector))
                        for each, vector in zip(entries, vectors, strict=True)
                    ],
                )
            self.connection.execute(
                sql.SQL(
                    "CREATE INDEX {} ON {} USING gist (description gist_trgm_ops)"
                ).format(sql.Identifier("catalog_description_trgm"), table)
            )
            started = time.perf_counter()
            self.connection.execute(
                sql.SQL(
                    "CREATE INDEX {} ON {} USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = {}, ef_construction = {})"
                ).format(
                    sql.Identifier("catalog_embedding_hnsw"),
                    table,
                    sql.Literal(HNSW_M),
                    sql.Literal(HNSW_EF_CONSTRUCTION),
                )
            )
            index_s = time.perf_counter() - started
            self.connection.execute(sql.SQL("ANALYZE {}").format(table))
        self.connection.execute(
            sql.SQL("SET hnsw.ef_search = {}").format(sql.Literal(EF_SEARCH))
        )
        return Build(embedding_s, index_s)

    def versions(self) -> ServerVersions:
        """The server and both extensions, or a `StoreError` naming the
        extension that is missing."""
        installed = {
            str(name): str(version)
            for name, version in self.connection.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname = ANY(%s)",
                (list(EXTENSIONS),),
            ).fetchall()
        }
        for extension in EXTENSIONS:
            if extension not in installed:
                raise StoreError(
                    f"extension {extension} is not installed in the database; "
                    f"run CREATE EXTENSION {extension} there first"
                )
        (server,) = self.connection.execute(
            "SELECT current_setting('server_version')"
        ).fetchone() or ("unknown",)
        return ServerVersions(
            postgres=str(server),
            pgvector=installed["vector"],
            pg_trgm=installed["pg_trgm"],
        )


def vector_literal(vector: Vector) -> str:
    """A vector as pgvector reads it, `[x,y,z]`, bound as a parameter and
    cast on the server, so no driver adapter is needed."""
    return "[" + ",".join(repr(each) for each in vector) + "]"


def machine() -> Machine:
    """The machine as the OS reports it, `unknown` where it does not."""
    return Machine(
        cpu=_cpu_model(),
        logical_cpus=os.cpu_count() or 0,
        memory_gib=_memory_gib(),
        kernel=platform.release(),
    )


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _memory_gib() -> float:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        return 0.0
    return pages * page_size / 2**30
