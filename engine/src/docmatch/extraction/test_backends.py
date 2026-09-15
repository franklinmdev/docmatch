"""Tests for choosing a backend and its model by name."""

import pytest

from docmatch.extraction import backends, gemini
from docmatch.extraction.extractor import ExtractionError
from docmatch.extraction.test_run import READING, FakeExtractor


def test_gemini_reads_with_its_own_default_when_no_model_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, object] = {}
    fake = FakeExtractor(answers=[READING])

    def backend(model: str, long_edge: int) -> FakeExtractor:
        built.update(model=model, long_edge=long_edge)
        return fake

    monkeypatch.setattr(gemini, "extractor", backend)

    assert backends.extractor("gemini", None, long_edge=1200) is fake
    assert built == {"model": "gemini-3.1-flash-lite", "long_edge": 1200}


def test_a_model_given_by_name_is_the_one_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []

    def backend(model: str, long_edge: int) -> FakeExtractor:
        built.append(model)
        return FakeExtractor(answers=[READING])

    monkeypatch.setattr(gemini, "extractor", backend)

    backends.extractor("gemini", "gemini-3.5-flash-lite", long_edge=1600)

    assert built == ["gemini-3.5-flash-lite"]


@pytest.mark.parametrize("backend", ["azure", "openai"])
def test_a_backend_not_wired_yet_is_refused_by_name(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")

    with pytest.raises(ExtractionError, match=f"the {backend} backend is not wired"):
        backends.extractor(backend, None, long_edge=1600)


def test_an_unpriced_model_is_refused_before_a_client_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key in the environment, so reaching the client would say so instead."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ExtractionError, match="no price is written down"):
        backends.extractor("gemini", "gemini-4-imaginary", long_edge=1600)


def test_every_backend_named_is_one_the_command_offers() -> None:
    assert backends.BACKENDS == ("gemini", "azure", "openai")
