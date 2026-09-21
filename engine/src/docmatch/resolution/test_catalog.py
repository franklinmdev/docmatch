"""Tests for the catalog and the exact queries on hand-built annotations:
seam 1 of the Phase 3 spec. Every description is made up."""

import hashlib
from collections.abc import Sequence

import pytest

from docmatch.docile.annotation import Annotation, DocumentMetadata, LineItemCell
from docmatch.resolution.catalog import (
    CatalogCounts,
    CollisionError,
    Entry,
    Query,
    build_catalog,
    exact_queries,
    mint,
)

BOX = (0.0, 0.0, 1.0, 1.0)


def document(*rows: Sequence[tuple[str, str]]) -> Annotation:
    """A document with one line per row, each row its fieldtype and text pairs."""
    return Annotation(
        fields=[],
        cells=[
            LineItemCell(
                fieldtype=fieldtype, text=text, page=0, bbox=BOX, line_item_id=line
            )
            for line, row in enumerate(rows, start=1)
            for fieldtype, text in row
        ],
        metadata=DocumentMetadata(
            page_count=1,
            page_sizes_at_200dpi=((1700, 2200),),
            source="synthetic",
            original_filename="synthetic",
        ),
    )


def described(*descriptions: str) -> Annotation:
    """A document whose lines carry a description and a quantity."""
    return document(
        *(
            (("line_item_description", text), ("line_item_quantity", "1"))
            for text in descriptions
        )
    )


def test_a_description_in_two_documents_is_an_entry_and_one_in_one_is_not() -> None:
    catalog = build_catalog(
        [
            described("Blue widget", "Red widget"),
            described("Blue widget", "Green widget"),
        ]
    )

    assert catalog.entries == (Entry(mint("blue widget"), "blue widget"),)
    assert catalog.counts == CatalogCounts(documents=2, lines=4, distinct=3, entries=1)


def test_a_description_repeated_inside_one_document_is_no_entry() -> None:
    catalog = build_catalog(
        [described("Blue widget", "Blue widget"), described("Red widget")]
    )

    assert catalog.entries == ()
    assert catalog.counts == CatalogCounts(documents=2, lines=3, distinct=2, entries=0)


def test_the_canonical_description_is_the_normalized_text() -> None:
    """Case and spacing never make two labels two descriptions, so the entry
    carries the normalized form, the one pairing already compares."""
    catalog = build_catalog([described("Blue  Widget"), described("blue widget\n")])

    assert [each.description for each in catalog.entries] == ["blue widget"]


def test_a_blank_description_is_no_description() -> None:
    catalog = build_catalog([described("  "), described("")])

    assert catalog.entries == ()
    assert catalog.counts.distinct == 0


def test_a_line_without_a_description_still_counts_as_a_line() -> None:
    catalog = build_catalog(
        [document((("line_item_quantity", "3"),)), described("Blue widget")]
    )

    assert catalog.counts == CatalogCounts(documents=2, lines=2, distinct=1, entries=0)


def test_the_sku_is_a_function_of_the_canonical_description_alone() -> None:
    digest = hashlib.sha256(b"blue widget").hexdigest()

    assert mint("blue widget") == f"SKU-{digest[:8]}"
    assert mint("blue widget") != mint("blue widget 2")


def test_entries_are_sorted_by_sku_whatever_the_documents_order() -> None:
    one = build_catalog(
        [described("Blue widget", "Red widget"), described("Red widget", "Blue widget")]
    )
    other = build_catalog(
        [described("Red widget", "Blue widget"), described("Blue widget", "Red widget")]
    )

    assert one == other
    assert [each.sku for each in one.entries] == sorted(
        each.sku for each in one.entries
    )


def test_a_sku_collision_raises_rather_than_merging_two_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("docmatch.resolution.catalog.mint", lambda _: "SKU-00000000")

    with pytest.raises(CollisionError, match="SKU-00000000"):
        build_catalog(
            [
                described("Blue widget", "Red widget"),
                described("Blue widget", "Red widget"),
            ]
        )


def test_a_pool_with_no_repeats_yields_an_empty_catalog() -> None:
    catalog = build_catalog([described("Blue widget"), described("Red widget")])

    assert catalog.entries == ()
    assert exact_queries(catalog) == ()


def test_each_entry_yields_one_exact_query_carrying_its_sku() -> None:
    catalog = build_catalog(
        [
            described("Blue widget", "Red widget"),
            described("Blue widget", "Red widget"),
        ]
    )

    assert exact_queries(catalog) == tuple(
        Query(each.description, each.sku, "exact") for each in catalog.entries
    )
    assert len(exact_queries(catalog)) == 2
