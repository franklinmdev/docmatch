"""The seed pool: which labeled documents cases are built from, and how many.

The per-type table seeds from every DocILE label, train and val, fixed in
code with no flag: a missing split is an error, so a partial download cannot
shrink the table silently, and the report gives documents and lines per
split (#75). The labels control and the end-to-end rows seed from the fixed
subset's documents, the only ones with saved readings (#62).

A document with no labeled lines seeds nothing, and is counted, so the
report can say plainly how many documents a row rests on (#62, #67).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from docmatch.docile.annotation import Annotation
from docmatch.docile.dataset import DocileDataset
from docmatch.matching.generator import Seed
from docmatch.matching.records import labeled_record

SPLITS = ("train", "val")
"""The splits the per-type table seeds from, every one required."""


@dataclass(frozen=True)
class PoolCount:
    """What one split, or the fixed subset, contributed."""

    name: str
    documents: int
    lines: int
    without_lines: int
    """Documents that seed nothing, having no labeled line."""


@dataclass(frozen=True)
class SeedPool:
    """Every seed the cases are built from, and the counts behind them."""

    counts: tuple[PoolCount, ...]
    seeds: tuple[Seed, ...]

    @property
    def documents(self) -> int:
        return sum(each.documents for each in self.counts)

    @property
    def without_lines(self) -> int:
        return sum(each.without_lines for each in self.counts)


def load_pool(dataset: DocileDataset, splits: Iterable[str] = SPLITS) -> SeedPool:
    """Every document of every split named, as seeds; a missing split raises."""
    counts: list[PoolCount] = []
    seeds: list[Seed] = []
    for split in splits:
        part = pool_from(
            split,
            (
                (document_id, dataset.annotation(document_id))
                for document_id in dataset.document_ids(split)
            ),
        )
        counts += part.counts
        seeds += part.seeds
    return SeedPool(tuple(counts), tuple(seeds))


def pool_from(name: str, annotations: Iterable[tuple[str, Annotation]]) -> SeedPool:
    """The documents given as one counted pool, those with lines as seeds."""
    seeds: list[Seed] = []
    documents = lines = without_lines = 0
    for document_id, annotation in annotations:
        documents += 1
        record = labeled_record(annotation)
        if record.lines:
            lines += len(record.lines)
            seeds.append(Seed(document_id, record))
        else:
            without_lines += 1
    return SeedPool((PoolCount(name, documents, lines, without_lines),), tuple(seeds))
