"""Tests for the Postgres working space: seam 2 of the Phase 3 spec. Skipped,
not failed, when no database answers; the precedence and the password
masking need none."""

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from docmatch.resolution.catalog import Entry, mint
from docmatch.resolution.conftest import BucketEmbedder
from docmatch.resolution.store import (
    DEFAULT_DATABASE_URL,
    DIMENSIONS,
    EF_SEARCH,
    HNSW_EF_CONSTRUCTION,
    HNSW_M,
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


def test_rebuild_drops_what_the_last_run_left(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild(
        [Entry(mint(each), each) for each in ("one", "two", "three")], embedder
    )
    store.rebuild([Entry(mint("four"), "four")], embedder)

    (count,) = store.connection.execute(
        f"SELECT count(*) FROM {store.schema}.catalog"
    ).fetchone() or (None,)
    assert count == 1


def test_rebuild_embeds_every_entry_into_the_vector_column_of_384(
    store: Store, embedder: BucketEmbedder
) -> None:
    """Every canonical description goes through the embedder in one batch at
    build time, and what it gave is what the column holds."""
    entries = [Entry(mint(each), each) for each in ("widget a", "widget b c")]

    store.rebuild(entries, embedder)

    row = store.connection.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = 'embedding'",
        (f"{store.schema}.catalog",),
    ).fetchone()
    assert row == (DIMENSIONS,)
    assert embedder.calls == [("widget a", "widget b c")]
    stored = {
        str(sku): tuple(float(each) for each in str(text).strip("[]").split(","))
        for sku, text in store.connection.execute(
            f"SELECT sku, embedding::text FROM {store.schema}.catalog"
        ).fetchall()
    }
    assert stored == {
        entry.sku: vector
        for entry, vector in zip(
            entries, embedder.embed(["widget a", "widget b c"]), strict=True
        )
    }


def test_rebuild_builds_the_gist_trigram_index(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild([Entry(mint("one"), "one")], embedder)

    definition = index_definition(store, "catalog_description_trgm")

    assert "USING gist (description gist_trgm_ops)" in definition


def test_rebuild_builds_the_hnsw_index_at_the_pinned_parameters(
    store: Store, embedder: BucketEmbedder
) -> None:
    store.rebuild([Entry(mint("one"), "one")], embedder)

    definition = index_definition(store, "catalog_embedding_hnsw")

    assert "USING hnsw (embedding vector_cosine_ops)" in definition
    assert f"m='{HNSW_M}'" in definition
    assert f"ef_construction='{HNSW_EF_CONSTRUCTION}'" in definition
    assert (HNSW_M, HNSW_EF_CONSTRUCTION) == (16, 64)


def test_a_store_sets_the_search_list_on_its_connection_before_any_rebuild(
    store: Store,
) -> None:
    """A fresh session would run the vector arm at pgvector's default 40;
    making a store over the connection is what pins it at 100."""
    (setting,) = store.connection.execute("SHOW hnsw.ef_search").fetchone() or (None,)

    assert setting == str(EF_SEARCH) == "100"
    assert store.versions().ef_search == "100"


def test_rebuild_reports_what_embedding_and_indexing_cost(
    store: Store, embedder: BucketEmbedder
) -> None:
    built = store.rebuild([Entry(mint("one"), "one")], embedder)

    assert built.embedding_s >= 0
    assert built.index_s >= 0


def index_definition(store: Store, name: str) -> str:
    (definition,) = store.connection.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
        (store.schema, name),
    ).fetchone() or (None,)
    assert isinstance(definition, str), name
    return definition


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
            bare.rebuild([Entry(mint("one"), "one")], BucketEmbedder())

    assert "CREATE EXTENSION" in str(caught.value)


def test_versions_reads_the_server_and_both_extensions(store: Store) -> None:
    versions = store.versions()

    assert versions.postgres.split(".")[0].isdigit()
    assert versions.pgvector.count(".") == 2
    assert versions.pg_trgm.count(".") == 1


def test_a_search_list_widened_for_a_statement_is_back_at_the_pin_after_it(
    store: Store,
) -> None:
    """A hybrid half deeper than the pin needs more candidates for its one
    statement; the vector arm after it runs at the pin again."""
    with store.search_list(125):
        (inside,) = store.connection.execute("SHOW hnsw.ef_search").fetchone() or (
            None,
        )

    assert inside == "125"
    assert store.versions().ef_search == str(EF_SEARCH)


def test_a_search_list_already_wide_enough_is_left_at_the_pin(
    store: Store,
) -> None:
    with store.search_list(50):
        (inside,) = store.connection.execute("SHOW hnsw.ef_search").fetchone() or (
            None,
        )

    assert inside == str(EF_SEARCH)
