"""Tests for the Postgres working space: seam 2 of the Phase 3 spec. Skipped,
not failed, when no database answers; the precedence and the password
masking need none."""

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from docmatch.resolution.catalog import Entry, mint
from docmatch.resolution.store import (
    DEFAULT_DATABASE_URL,
    DIMENSIONS,
    Store,
    StoreError,
    connect,
    machine,
    resolve_database_url,
    without_password,
)


def test_the_flag_beats_the_environment_beats_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCMATCH_DATABASE_URL", raising=False)
    assert resolve_database_url(None) == DEFAULT_DATABASE_URL

    monkeypatch.setenv("DOCMATCH_DATABASE_URL", "postgresql://host/fromenv")
    assert resolve_database_url(None) == "postgresql://host/fromenv"
    assert resolve_database_url("postgresql://host/flag") == "postgresql://host/flag"

    monkeypatch.setenv("DOCMATCH_DATABASE_URL", "")
    assert resolve_database_url(None) == DEFAULT_DATABASE_URL


def test_a_database_that_does_not_answer_is_named_without_its_password() -> None:
    with pytest.raises(StoreError) as caught:
        connect("postgresql://someone:secret@localhost:1/nothing")

    message = str(caught.value)
    assert "no database answers at" in message
    assert "dbname=nothing" in message
    assert "secret" not in message


def test_a_string_that_is_not_a_url_is_reported_as_such() -> None:
    with pytest.raises(StoreError, match="not a database url"):
        connect("postgresql://localhost:1/nothing?nonsense=1")


def test_without_password_keeps_the_rest_of_the_connection() -> None:
    assert without_password("postgresql:///docmatch") == "dbname=docmatch"
    assert without_password("dbname=x password=y") == "dbname=x"
    assert without_password("=") == "an unreadable database url"


def test_machine_reports_what_the_os_gives() -> None:
    reported = machine()

    assert reported.logical_cpus >= 1
    assert reported.memory_gib > 0
    assert reported.kernel
    assert reported.cpu


def test_rebuild_drops_what_the_last_run_left(store: Store) -> None:
    store.rebuild([Entry(mint(each), each) for each in ("one", "two", "three")])
    store.rebuild([Entry(mint("four"), "four")])

    (count,) = store.connection.execute(
        f"SELECT count(*) FROM {store.schema}.catalog"
    ).fetchone() or (None,)
    assert count == 1


def test_rebuild_leaves_the_table_with_a_null_vector_column_of_384(
    store: Store,
) -> None:
    store.rebuild([Entry(mint("one"), "one")])

    row = store.connection.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = 'embedding'",
        (f"{store.schema}.catalog",),
    ).fetchone()
    (nulls,) = store.connection.execute(
        f"SELECT count(*) FROM {store.schema}.catalog WHERE embedding IS NULL"
    ).fetchone() or (None,)

    assert row == (DIMENSIONS,)
    assert nulls == 1


def test_rebuild_builds_the_gist_trigram_index(store: Store) -> None:
    store.rebuild([Entry(mint("one"), "one")])

    (definition,) = store.connection.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
        (store.schema, "catalog_description_trgm"),
    ).fetchone() or (None,)

    assert isinstance(definition, str)
    assert "USING gist (description gist_trgm_ops)" in definition


def test_a_missing_extension_is_named_without_a_traceback(database_url: str) -> None:
    """`template1` is a database on the same cluster where nobody created
    the extensions, locally or in CI; if someone has, there is nothing to
    pin here."""
    parts = {key: str(value) for key, value in conninfo_to_dict(database_url).items()}
    parts["dbname"] = "template1"
    try:
        connection = connect(make_conninfo("", **parts))
    except StoreError as error:
        pytest.skip(str(error))
    with connection:
        bare = Store(connection, "resolution_test")
        installed = {
            str(name)
            for (name,) in connection.execute(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('vector', 'pg_trgm')"
            ).fetchall()
        }
        if {"vector", "pg_trgm"} <= installed:
            pytest.skip("template1 has both extensions here")

        with pytest.raises(StoreError, match="is not installed") as caught:
            bare.rebuild([Entry(mint("one"), "one")])

    assert "CREATE EXTENSION" in str(caught.value)


def test_versions_reads_the_server_and_both_extensions(store: Store) -> None:
    versions = store.versions()

    assert versions.postgres.split(".")[0].isdigit()
    assert versions.pgvector.count(".") == 2
    assert versions.pg_trgm.count(".") == 1
