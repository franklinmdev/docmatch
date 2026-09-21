"""Tests for the model interface: the fake behind it, and the one failure the
real loader reports. No weights are loaded; the loader is driven offline
against a repo id nothing has cached, so nothing is downloaded either."""

import pytest

from docmatch.resolution import models
from docmatch.resolution.conftest import BucketEmbedder
from docmatch.resolution.models import ModelError, load_models
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


def test_a_model_that_cannot_be_loaded_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline, against a repo id nothing has cached: the hub raises its
    OSError, and the command reports the model and revision it wanted the
    way it reports a database that does not answer. The environment
    variable is read at import time, so the constant is set too in case an
    earlier test imported the hub."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    import huggingface_hub.constants as constants

    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)
    monkeypatch.setattr(models, "EMBEDDER", "docmatch-tests/no-such-embedder")

    with pytest.raises(ModelError) as caught:
        load_models()

    message = str(caught.value)
    assert message.startswith(
        "the embedder docmatch-tests/no-such-embedder at "
        f"{models.EMBEDDER_REVISION} could not be loaded: "
    )
