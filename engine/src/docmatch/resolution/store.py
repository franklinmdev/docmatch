"""Postgres as the working space the arms run over.

Every run drops and recreates schema `resolution`, creates one table of SKU,
canonical description and a 384-dimension vector column, builds the GiST
trigram index, inserts the entries and leaves the schema in place afterwards
for inspection (#121). Nothing is cached: about 27 s of rebuild against about
13 min of measurement, and a run at the linked commit then reproduces the
row, catalog included. The vector column is left null here; #129 fills it.

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
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from docmatch.resolution.catalog import Entry, ResolutionError

DATABASE_URL_VARIABLE = "DOCMATCH_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql:///docmatch"

SCHEMA = "resolution"
"""The schema every run rebuilds; tests build their catalogs in another."""

DIMENSIONS = 384
"""The embedder's output width, `all-MiniLM-L6-v2` (#112)."""

EXTENSIONS = ("vector", "pg_trgm")
"""Both required to exist before a run."""


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
class Store:
    """The catalog in one schema of one database, over one connection."""

    connection: psycopg.Connection[tuple[object, ...]]
    schema: str = SCHEMA

    def rebuild(self, entries: Sequence[Entry]) -> None:
        """Drop whatever the last run left, and build the catalog afresh.

        The extensions are checked first, so a missing one is reported as
        such rather than as a type or an operator class that does not exist.
        """
        self.versions()
        schema = sql.Identifier(self.schema)
        table = sql.Identifier(self.schema, "catalog")
        index = sql.Identifier("catalog_description_trgm")
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
                    "embedding vector({}))"
                ).format(table, sql.Literal(DIMENSIONS))
            )
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    sql.SQL("INSERT INTO {} (sku, description) VALUES (%s, %s)").format(
                        table
                    ),
                    [(each.sku, each.description) for each in entries],
                )
            self.connection.execute(
                sql.SQL(
                    "CREATE INDEX {} ON {} USING gist (description gist_trgm_ops)"
                ).format(index, table)
            )
            self.connection.execute(sql.SQL("ANALYZE {}").format(table))

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
