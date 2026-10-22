"""Reproducing a row: the matching per-type table against a committed aggregate.

The per-type table is the one benchmark row a reader can regenerate from a
clean checkout. It needs the DocILE labels and nothing else: no saved run, no
database, no model and no key, since every case is rebuilt from the labels on
one pinned seed. The extraction and end-to-end rows need saved runs, which
are vendor output and stay on the machine that made them (rule 6).

So the table's cells are committed, beside the regression gate's aggregate,
in two halves: `docile`, computed from the real labels and equal to the
README's table, and `synthetic`, computed from the committed fixture, which
is the smoke check a reader runs before downloading DocILE and CI runs on
every push. Each half carries the seed its cases were drawn with and the
commit it was produced on. A half is written by the same code that renders
the table, `docmatch reproduce <half> --write`, never by hand.

The comparison is exact. Matching from labels under the pinned seed is
deterministic, so there is no tolerance: any cell that differs is a finding,
listed with both values. The commit a half was produced on is not a cell, so
a later commit that computes the same table matches.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from docmatch.docile.dataset import DocileDataset
from docmatch.matching.generator import (
    INJECTED_TYPES,
    SEED,
    documents_carrying,
    generate,
)
from docmatch.matching.pool import SPLITS, SeedPool, load_pool
from docmatch.matching.scoring import Table, score_matched

TABLES = Path("benchmark/matching.json")
"""Where the committed per-type tables live, from the repository root, beside
the regression gate's aggregate."""

HalfName = Literal["docile", "synthetic"]
HALVES: tuple[HalfName, ...] = ("docile", "synthetic")

FIXTURE = Path(__file__).parents[2] / "tests" / "evals" / "synthetic"
"""The committed synthetic fixture, the synthetic half's data directory when
none is given."""

PACKAGE = "engine/src/docmatch"


class ReproduceError(Exception):
    """A matching aggregate the command cannot read or write."""


class TypeCells(BaseModel):
    """One discrepancy type's row of the per-type table."""

    precision: float
    recall: float
    n: int
    """How many findings of the type were injected."""
    documents: int
    """How many seeds could carry the type."""


class Half(BaseModel):
    """The per-type table over one dataset, and what produced it."""

    per_type: dict[str, TypeCells]
    """One row per injected type, in the generator's order."""
    clean_case_false_positive_rate: float
    clean_cases: int
    clean_false_positives: int
    """Clean cases with at least one finding."""
    seed: int
    """The seed every case was drawn with."""
    produced_on: str
    """The commit the half was written on: where the cells came from, and the
    one field the comparison does not read."""


Tables = dict[str, Half]
TABLES_FORMAT = TypeAdapter(Tables)


def measure(dataset: DocileDataset) -> tuple[SeedPool, Table]:
    """The per-type table's cases from every label of both splits, matched and
    scored: the seed pool and the table `docmatch match` prints."""
    pool = load_pool(dataset, SPLITS)
    return pool, table_of(pool)


def table_of(pool: SeedPool) -> Table:
    """Every case the pinned seed draws from the pool, matched and scored."""
    return score_matched(generate(pool.seeds, seed=SEED))


def per_type_cells(pool: SeedPool, table: Table) -> dict[str, TypeCells]:
    """The per-type table's rows, injected types only, in their order."""
    return {
        each.type: TypeCells(
            precision=each.precision,
            recall=each.recall,
            n=each.n,
            documents=documents_carrying(pool.seeds, each.type),
        )
        for each in table.per_type
        if each.type in INJECTED_TYPES
    }


def half_of(pool: SeedPool, table: Table, commit: str) -> Half:
    """The table as a half, with the seed `table_of` draws with."""
    return Half(
        per_type=per_type_cells(pool, table),
        clean_case_false_positive_rate=table.clean_false_positive_rate,
        clean_cases=table.clean_cases,
        clean_false_positives=table.clean_false_positives,
        seed=SEED,
        produced_on=commit,
    )


Value = float | int | str


@dataclass(frozen=True)
class Difference:
    """One cell the computed table does not share with the committed half."""

    cell: str
    committed: Value
    computed: Value


def compare(committed: Half, computed: Half) -> tuple[Difference, ...]:
    """Every cell that differs, exactly, in the table's order: each type's
    precision, recall, n and documents, then the clean cases, then the seed."""
    return tuple(
        Difference(cell, before, after)
        for cell, before, after in _cells(committed, computed)
        if before != after
    )


_TYPE_CELLS = ("precision", "recall", "n", "documents")


def _cells(committed: Half, computed: Half) -> Iterator[tuple[str, Value, Value]]:
    extra = [each for each in computed.per_type if each not in committed.per_type]
    types = [*committed.per_type, *extra]
    for type_ in types:
        before = committed.per_type.get(type_)
        after = computed.per_type.get(type_)
        if before is None:
            yield type_, "not in the aggregate", "computed"
        elif after is None:
            yield type_, "in the aggregate", "not computed"
        else:
            for name in _TYPE_CELLS:
                yield f"{type_} {name}", getattr(before, name), getattr(after, name)
    yield (
        "clean-case false-positive rate",
        committed.clean_case_false_positive_rate,
        computed.clean_case_false_positive_rate,
    )
    yield "clean cases", committed.clean_cases, computed.clean_cases
    yield (
        "clean cases with a finding",
        committed.clean_false_positives,
        computed.clean_false_positives,
    )
    yield "seed", committed.seed, computed.seed


def read_tables(path: Path) -> Tables:
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError as error:
        raise ReproduceError(
            f"no matching aggregate at {path}; run from the repository root, "
            "or name it with --tables"
        ) from error
    try:
        return TABLES_FORMAT.validate_json(text)
    except ValidationError as error:
        raise ReproduceError(
            f"{path} is not a matching aggregate: expected a JSON object keyed "
            f"by {' or '.join(HALVES)}, each holding a per-type table. {error}"
        ) from error


def write_half(path: Path, name: HalfName, half: Half) -> None:
    """Set one half, keep the other, in the halves' order."""
    tables = read_tables(path) if path.exists() else {}
    tables[name] = half
    ordered: Tables = {each: tables[each] for each in HALVES if each in tables}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TABLES_FORMAT.dump_json(ordered, indent=2) + b"\n")


def engine_path(path: str) -> bool:
    """Whether a path is engine code a table could have come from: the
    package, tests and fixtures aside."""
    parts = PurePosixPath(path).parts
    name = parts[-1]
    return (
        path.startswith(f"{PACKAGE}/")
        and not name.startswith("test_")
        and name != "conftest.py"
        and "fixtures" not in parts
    )
