"""Tests for choosing a backend and its model by name."""

import pytest

from docmatch.extraction import azure, backends, gemini, openai
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


def test_an_unknown_backend_is_refused_by_name() -> None:
    with pytest.raises(ExtractionError, match="no backend named anthropic"):
        backends.extractor("anthropic", None, long_edge=1600)


def test_openai_reads_with_its_own_default_when_no_model_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, object] = {}
    fake = FakeExtractor(answers=[READING])

    def backend(model: str, long_edge: int) -> FakeExtractor:
        built.update(model=model, long_edge=long_edge)
        return fake

    monkeypatch.setattr(openai, "extractor", backend)

    assert backends.extractor("openai", None, long_edge=1200) is fake
    assert built == {"model": "gpt-5.6-luna", "long_edge": 1200}


def test_an_unpriced_openai_model_is_refused_before_a_client_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key in the environment, so reaching the client would say so instead."""
    monkeypatch.delenv(openai.KEY_VARIABLE, raising=False)

    with pytest.raises(ExtractionError, match="no price is written down"):
        backends.extractor("openai", "gpt-7-imaginary", long_edge=1600)


def test_azure_reads_with_its_own_default_when_no_model_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []
    fake = FakeExtractor(answers=[READING])

    def backend(model: str) -> FakeExtractor:
        built.append(model)
        return fake

    monkeypatch.setattr(azure, "extractor", backend)

    assert backends.extractor("azure", None, long_edge=1600) is fake
    assert built == ["prebuilt-invoice"]


def test_azure_passes_a_model_given_by_name_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []

    def backend(model: str) -> FakeExtractor:
        built.append(model)
        return FakeExtractor(answers=[READING])

    monkeypatch.setattr(azure, "extractor", backend)

    backends.extractor("azure", "prebuilt-invoice", long_edge=1600)

    assert built == ["prebuilt-invoice"]


def test_an_unpriced_azure_model_is_refused_before_a_client_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No endpoint or key in the environment, so reaching the client would say so."""
    monkeypatch.delenv(azure.ENDPOINT_VARIABLE, raising=False)
    monkeypatch.delenv(azure.KEY_VARIABLE, raising=False)

    with pytest.raises(ExtractionError, match="no price is written down"):
        backends.extractor("azure", "prebuilt-receipt", long_edge=1600)


def test_an_unpriced_model_is_refused_before_a_client_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key in the environment, so reaching the client would say so instead."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ExtractionError, match="no price is written down"):
        backends.extractor("gemini", "gemini-4-imaginary", long_edge=1600)


def test_every_backend_named_is_one_the_command_offers() -> None:
    assert backends.BACKENDS == ("gemini", "azure", "openai")


def test_only_the_vision_backends_render_pages() -> None:
    assert backends.RENDERING == ("gemini", "openai")
