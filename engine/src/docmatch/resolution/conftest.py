"""Fixtures for the resolution tests: the models, faked.

No weights are ever loaded here. The arms take the models through the
interface in `models`, and these fakes are deterministic and built to tie,
so the ordering a test pins is the arm's and never the model's. The
database fixtures live one level up, beside the CLI's.
"""

import hashlib
from collections.abc import Sequence

import pytest

from docmatch.resolution.models import Loaded, ModelVersions, Vector
from docmatch.resolution.store import DIMENSIONS


class BucketEmbedder:
    """A deterministic embedder built to tie: every word is one dimension of
    the 384, the vocabulary's words the first dimensions in the order given
    and any other word one of the rest by its hash, and a text is the count
    of its words in each. `widget a` and `widget b` then sit at the same
    cosine distance from `widget`, exactly, which is what pins an ordering on
    ties. A word's dimension is a function of the word alone, so a query
    and an entry embed the same way and the catalog is never read."""

    def __init__(self, *vocabulary: str) -> None:
        if len(vocabulary) >= DIMENSIONS:
            raise ValueError("the vocabulary must leave dimensions for other words")
        self._vocabulary = {word: index for index, word in enumerate(vocabulary)}
        self.calls: list[tuple[str, ...]] = []
        """Every batch of texts asked for, in order."""

    def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        self.calls.append(tuple(texts))
        return tuple(self._one(text) for text in texts)

    def _one(self, text: str) -> Vector:
        vector = [0.0] * DIMENSIONS
        for word in text.split():
            vector[self._dimension(word)] += 1.0
        return tuple(vector)

    def _dimension(self, word: str) -> int:
        if word in self._vocabulary:
            return self._vocabulary[word]
        digest = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        return len(self._vocabulary) + digest % (DIMENSIONS - len(self._vocabulary))


FAKE_VERSIONS = ModelVersions(
    embedder="fake/bucket-embedder",
    embedder_revision="0000000000000000000000000000000000000000",
    sentence_transformers="0.0.0",
    torch="0.0.0",
    torch_threads=1,
)


def fake_loader(embedder: BucketEmbedder | None = None, load_s: float = 0.5) -> Loaded:
    """What the run gets in place of `load_models`: the fake, made-up versions
    and a made-up load time."""
    return Loaded(embedder or BucketEmbedder(), FAKE_VERSIONS, load_s)


@pytest.fixture
def embedder() -> BucketEmbedder:
    """The fake with `widget` and the letters as its first dimensions."""
    return BucketEmbedder("widget", *"abcdefghijklmnopqrstuvwxyz")
