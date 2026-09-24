"""The loop's tables, one Postgres schema per loop run.

Three tables, and the documents table is the only queue (ADR 0002):

- `documents`: the upload's bytes and case under the unique digest of its
  content, the status, the output saved at each checkpoint, and the lease a
  worker holds while it moves the document on.
- `transitions`: every status change, append-only. A trigger refuses an
  update or a delete, so nothing rewrites a document's history (#150).
- `vendor_calls`: one row per request sent to a backend's vendor, written
  and committed on its own as the request returns, so a reading paid for and
  never saved still counts (#157).

`serve` creates the schema when it is missing and keeps it when it is there,
so a restarted server picks up where the last one stood, and a loop run's
schema is kept afterwards for a reviewer (#164). The connection precedence and
the driver are the resolution store's: psycopg 3.3.6, parameters bound with
`%s`, the schema set as the connection's `search_path` so every statement
names its tables plainly.
"""

import psycopg
from psycopg import sql

from docmatch.resolution.store import connect

Connection = psycopg.Connection[tuple[object, ...]]

TABLES = """
CREATE TABLE IF NOT EXISTS documents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    digest text NOT NULL UNIQUE,
    invoice bytea NOT NULL,
    pages integer NOT NULL,
    "case" jsonb NOT NULL,
    status text NOT NULL,
    reading jsonb,
    gate jsonb,
    resolution jsonb,
    match jsonb,
    lease_until timestamptz,
    taken_at timestamptz
);
CREATE TABLE IF NOT EXISTS transitions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document bigint NOT NULL REFERENCES documents (id),
    from_status text,
    to_status text NOT NULL,
    taken_at timestamptz,
    committed_at timestamptz NOT NULL,
    actor text NOT NULL CHECK (actor IN ('system', 'reviewer')),
    reasons text[] NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS vendor_calls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document bigint NOT NULL REFERENCES documents (id),
    status text NOT NULL,
    attempt integer NOT NULL,
    units jsonb NOT NULL,
    cost numeric NOT NULL,
    served_model text,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    response text,
    error text
);
CREATE OR REPLACE FUNCTION transitions_are_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'transitions are append-only';
END
$$;
CREATE OR REPLACE TRIGGER transitions_are_append_only
    BEFORE UPDATE OR DELETE ON transitions
    FOR EACH STATEMENT EXECUTE FUNCTION transitions_are_append_only();
"""
"""Every table, written so running it again on a kept schema changes nothing."""


def prepare(url: str, schema: str) -> None:
    """The loop's schema and its tables, created when missing and left as
    they are when there. Run once, before the server and its worker open
    their connections, so no two of them race to create a table."""
    with connect(url) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
        )
        _search(connection, schema)
        connection.execute(TABLES)


def open_schema(url: str, schema: str) -> Connection:
    """One connection in autocommit on a prepared schema. A write that must
    land together with another opens its own transaction."""
    connection = connect(url)
    connection.autocommit = True
    _search(connection, schema)
    return connection


def _search(connection: Connection, schema: str) -> None:
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))


def drop_schema(connection: Connection, schema: str) -> None:
    """The schema and everything in it, for a test that starts afresh."""
    connection.execute(
        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
    )
