"""The catalog and the query set, built from labels alone.

A catalog entry is a minted SKU plus a canonical description and nothing more
(#117). The canonical description is a labeled description after the text
normalization pairing already compares, and the catalog is every one that
appears in two or more train documents: no draw, no seed, no size parameter,
so the catalog is a function of the labels. A description repeated inside
one document only is no entry. Near-duplicates are kept apart, since two
descriptions that differ only in their digits are two items.

The SKU is `SKU-` plus the first eight hex characters of the SHA-256 of the
canonical description, so it depends on its own entry alone and never shifts
when the catalog changes. A collision raises rather than merging two entries
into one truth. DocILE's own line codes play no part: 225 of the 1,920
entries carry one on every line, 151 codes serve more than one description,
and rule 6 keeps them out of any output.

A query is one normalized description and nothing else, carrying the SKU of
the entry it was generated from as its truth (#119). This module builds the
exact queries, one per entry with its canonical description unchanged; the
noisy variants, the split and the out-of-catalog draw are later tickets.

Everything here is pure: annotations in, entries and queries out. The
builder never reads the dataset itself; `load_catalog` does, from the train
split only, and a missing split is an error rather than a smaller catalog
(#75).
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from docmatch.docile.annotation import Annotation
from docmatch.docile.dataset import DocileDataset
from docmatch.matching.records import listed
from docmatch.metrics.line_items import labeled_line_items

SPLIT = "train"
"""The split the catalog seeds from; val is out entirely (#117)."""

REPEATS = 2
"""How many documents a description has to appear in to be an entry."""


class ResolutionError(Exception):
    """A resolution run that cannot proceed, reported without a traceback."""


class CollisionError(ResolutionError):
    """Two canonical descriptions minted the same SKU."""


@dataclass(frozen=True)
class Entry:
    """One thing resolution can name."""

    sku: str
    description: str


@dataclass(frozen=True)
class CatalogCounts:
    """What the catalog came from, printed so a partial download shows."""

    documents: int
    lines: int
    """Labeled lines over every document, whether or not they carry a
    description."""
    distinct: int
    """Distinct canonical descriptions over every document."""
    entries: int


@dataclass(frozen=True)
class Catalog:
    """Every entry resolution can name, and the counts it was built from."""

    entries: tuple[Entry, ...]
    """Sorted by SKU: the order carries no meaning, and a fixed one keeps a
    rebuild inserting the same rows in the same order."""
    counts: CatalogCounts


QueryKind = Literal["exact"]
"""How a query was made from its entry. The noisy kinds join in #128."""


@dataclass(frozen=True)
class Query:
    """One normalized description sent to an arm, and what the right answer
    is: the SKU of the entry it was generated from."""

    text: str
    sku: str
    kind: QueryKind


def mint(description: str) -> str:
    """The SKU a canonical description mints, a function of it alone."""
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
    return f"SKU-{digest[:8]}"


def build_catalog(annotations: Iterable[Annotation]) -> Catalog:
    """Every canonical description that appears in `REPEATS` or more of the
    documents given, minted as an entry, with the counts it came from."""
    documents = lines = 0
    seen_in: dict[str, int] = {}
    for annotation in annotations:
        documents += 1
        rows = labeled_line_items(annotation)
        lines += len(rows)
        carried = {text for row in rows for text in listed(row, "description")}
        for text in carried:
            seen_in[text] = seen_in.get(text, 0) + 1
    repeated = sorted(text for text, count in seen_in.items() if count >= REPEATS)
    entries: dict[str, Entry] = {}
    for description in repeated:
        sku = mint(description)
        if sku in entries:
            raise CollisionError(f"two descriptions mint {sku}")
        entries[sku] = Entry(sku, description)
    return Catalog(
        tuple(entries[sku] for sku in sorted(entries)),
        CatalogCounts(documents, lines, len(seen_in), len(entries)),
    )


def load_catalog(dataset: DocileDataset) -> Catalog:
    """The catalog from the train split; a missing split raises."""
    return build_catalog(
        dataset.annotation(document_id)
        for document_id in sorted(dataset.document_ids(SPLIT))
    )


def exact_queries(catalog: Catalog) -> tuple[Query, ...]:
    """One exact query per entry: its canonical description unchanged."""
    return tuple(Query(each.description, each.sku, "exact") for each in catalog.entries)
