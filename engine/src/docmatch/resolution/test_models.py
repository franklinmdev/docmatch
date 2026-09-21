"""Tests for the model interface: the fake behind it, and the one failure the
real loader reports. No weights are loaded; the loader is driven offline
against a repo id nothing has cached, so nothing is downloaded either."""

import pytest

from docmatch.resolution import models
from docmatch.resolution.conftest import BucketEmbedder, WordReranker
from docmatch.resolution.models import ModelError, load_models, load_reranker
from docmatch.resolution.store import DIMENSIONS


def test_the_fake_embeds_a_text_as_the_count_of_its_words_per_dimension() -> None:
    embedder = BucketEmbedder("widget", "a")

    (widget, widget_a, widget_a_a) = embedder.embed(
        ["widget", "widget a", "a widget a"]
    )

    assert len(widget) == DIMENSIONS
    assert widget[:2] == (1.0, 0.0)
    assert widget_a[:2] == (1.0, 1.0)
    assert widget_a_a[:2] == (1.0, 2.0)
    assert sum(widget) == 1.0


def test_the_fake_puts_a_word_outside_the_vocabulary_in_a_later_dimension() -> None:
    embedder = BucketEmbedder("widget")

    (gadget,) = embedder.embed(["gadget"])

    assert gadget[0] == 0.0
    assert sum(gadget) == 1.0
    assert gadget == embedder.embed(["gadget"])[0], "a function of the word alone"


def test_the_fake_refuses_a_vocabulary_that_fills_every_dimension() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        BucketEmbedder(*(f"w{index}" for index in range(DIMENSIONS)))


def test_the_fake_reranker_scores_a_pair_minus_the_words_it_does_not_share() -> None:
    reranker = WordReranker()

    scores = reranker.score(
        [("widget", "widget"), ("widget", "widget a"), ("widget", "widget b c")]
    )

    assert scores == (0.0, -1.0, -2.0)
    assert reranker.calls == [
        (("widget", "widget"), ("widget", "widget a"), ("widget", "widget b c"))
    ]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hub offline, so a repo id nothing has cached raises its OSError
    and nothing is downloaded. The environment variable is read at import
    time, so the constant is set too in case an earlier test imported the
    hub."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    import huggingface_hub.constants as constants

    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)


@pytest.mark.usefixtures("offline")
def test_an_embedder_that_cannot_be_loaded_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command reports the model and revision it wanted the way it
    reports a database that does not answer."""
    monkeypatch.setattr(models, "EMBEDDER", "docmatch-tests/no-such-embedder")

    with pytest.raises(ModelError) as caught:
        load_models()

    message = str(caught.value)
    assert message.startswith(
        "the embedder docmatch-tests/no-such-embedder at "
        f"{models.EMBEDDER_REVISION} could not be loaded: "
    )


@pytest.mark.usefixtures("offline")
def test_a_reranker_that_cannot_be_loaded_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loaded on its own so the test needs no embedder in the cache."""
    monkeypatch.setattr(models, "RERANKER", "docmatch-tests/no-such-reranker")

    with pytest.raises(ModelError) as caught:
        load_reranker()

    assert str(caught.value).startswith(
        "the reranker docmatch-tests/no-such-reranker at "
        f"{models.RERANKER_REVISION} could not be loaded: "
    )


def test_the_reranker_is_the_one_112_pinned() -> None:
    assert models.RERANKER == "cross-encoder/ms-marco-MiniLM-L6-v2"
    assert models.RERANKER_REVISION == "233902d25c440f23af6f7d6e94d2946bac0bee0a"
