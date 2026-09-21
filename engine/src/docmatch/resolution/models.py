"""The models behind the arms, an embedder and a reranker, behind one interface.

An embedder maps texts to vectors and a reranker maps (query, description)
pairs to scores; the arms take either through the interface, so the tests
drive them with deterministic fakes and the command loads the real pair in
one place, here (#129).

The embedder is `sentence-transformers/all-MiniLM-L6-v2` pinned to its full
commit (#112), 384 dimensions, its output unit-normalized (#120 measured norm
1.000000) with cosine kept as the operator all the same, rule 7. Torch is
pinned at ten threads: #120 measured embedding at 12.35 ms on one thread
against 14.45 on ten, but reranking 25 pairs at 272 ms against 102, so ten
is the better setting for the pair. Each model's load is timed once and
reported beside the table, never inside a query's latency.

The reranker is `cross-encoder/ms-marco-MiniLM-L6-v2` pinned to its full
commit (#112, #131). It scores a query's candidates in one `predict` call,
the batch as wide as the candidates, so N pairs are one forward pass. Its
scores are the raw logits, the activation replaced by the identity: the
model's default sigmoid is monotone, so the order is the same, but it
saturates in float32 for a strong match and would manufacture ties the
logits do not have.

sentence-transformers 6.1.0, docs read 2026-09-21
(sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html):
`revision` takes a commit id, `device` names the device rather than probing
for a GPU, and `encode` returns one row per text. `CrossEncoder` takes the
same `revision` and `device`, and `predict` takes `batch_size` (default 32)
and `activation_fn`, which replaces the model's own activation (default a
sigmoid for one label), read in the installed 6.1.0's docstrings on
2026-09-21. torch 2.14.0+cpu, docs read 2026-09-21
(docs.pytorch.org/docs/2.14/generated/torch.set_num_threads.html):
`set_num_threads` sets the intra-op threads and must be called before any
eager code runs, so it is called before the model is loaded. Both libraries
are imported inside the loader, so `docmatch match` and every test that
fakes the models never pay for torch.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import TYPE_CHECKING, Protocol

from docmatch.resolution.catalog import ResolutionError

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

EMBEDDER = "sentence-transformers/all-MiniLM-L6-v2"
"""The embedder, by its Hugging Face repo id (#112)."""

EMBEDDER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
"""The embedder's full commit as the repo records it, passed as `revision=`
and read by CI as its cache key, so the row and the cache both name it."""

RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"
"""The reranker, by its Hugging Face repo id (#112)."""

RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
"""The reranker's full commit, read from the hub's API on 2026-09-21 and
equal to the one #112 read, passed as `revision=` and read by CI as its
cache key beside the embedder's."""

TORCH_THREADS = 10
"""Intra-op threads torch runs the models on, pinned (#120)."""

BATCH = 64
"""Texts per forward pass when the catalog is embedded at build time."""

Vector = tuple[float, ...]


class ModelError(ResolutionError):
    """A model that could not be loaded: not cached and not reachable, or not
    at the pinned revision. A report, not a traceback, like a database that
    does not answer."""


class Embedder(Protocol):
    """Texts to vectors, one per text, in the order given."""

    def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


class Reranker(Protocol):
    """(query, description) pairs to scores, one per pair, higher better."""

    def score(self, pairs: Sequence[tuple[str, str]]) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class ModelVersions:
    """What was loaded, read at run time so a row names it unambiguously."""

    embedder: str
    embedder_revision: str
    reranker: str
    reranker_revision: str
    sentence_transformers: str
    torch: str
    torch_threads: int


@dataclass(frozen=True)
class Loaded:
    """The models the run drives, what they are, and what loading cost once."""

    embedder: Embedder
    reranker: Reranker
    versions: ModelVersions
    embedder_load_s: float
    """Loading the embedder, torch's import and thread setting with it."""
    reranker_load_s: float
    """Loading the reranker, after the embedder."""


ModelLoader = Callable[[], Loaded]
"""How a run gets its models: the command passes `load_models`, tests a fake."""


class SentenceTransformerEmbedder:
    """The real embedder, `encode` over one batch of texts at a time."""

    def __init__(self, model: "SentenceTransformer") -> None:
        self._model = model

    def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        rows = self._model.encode(
            list(texts),
            batch_size=BATCH,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return tuple(tuple(float(each) for each in row) for row in rows.tolist())


class CrossEncoderReranker:
    """The real reranker, `predict` over every pair in one batch, raw logits."""

    def __init__(self, model: "CrossEncoder") -> None:
        self._model = model

    def score(self, pairs: Sequence[tuple[str, str]]) -> tuple[float, ...]:
        import torch

        scores = self._model.predict(
            list(pairs),
            batch_size=max(len(pairs), 1),
            activation_fn=torch.nn.Identity(),
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return tuple(float(each) for each in scores.tolist())


def load_models() -> Loaded:
    """The real pair from the local cache or the hub, at the pinned
    revisions, torch's threads set first, each timed once."""
    started = time.perf_counter()
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(TORCH_THREADS)
    try:
        model = SentenceTransformer(EMBEDDER, revision=EMBEDDER_REVISION, device="cpu")
    except OSError as error:
        # The hub's errors, a missing cache entry offline and a revision the
        # repo does not have, are all OSError subclasses.
        raise ModelError(
            f"the embedder {EMBEDDER} at {EMBEDDER_REVISION} could not be "
            f"loaded: {error}"
        ) from None
    embedder_load_s = time.perf_counter() - started
    started = time.perf_counter()
    reranker = load_reranker()
    return Loaded(
        embedder=SentenceTransformerEmbedder(model),
        reranker=reranker,
        versions=ModelVersions(
            embedder=EMBEDDER,
            embedder_revision=EMBEDDER_REVISION,
            reranker=RERANKER,
            reranker_revision=RERANKER_REVISION,
            sentence_transformers=version("sentence-transformers"),
            torch=str(torch.__version__),
            torch_threads=torch.get_num_threads(),
        ),
        embedder_load_s=embedder_load_s,
        reranker_load_s=time.perf_counter() - started,
    )


def load_reranker() -> CrossEncoderReranker:
    """The reranker from the local cache or the hub, at the pinned revision."""
    from sentence_transformers import CrossEncoder

    try:
        model = CrossEncoder(RERANKER, revision=RERANKER_REVISION, device="cpu")
    except OSError as error:
        raise ModelError(
            f"the reranker {RERANKER} at {RERANKER_REVISION} could not be "
            f"loaded: {error}"
        ) from None
    return CrossEncoderReranker(model)
