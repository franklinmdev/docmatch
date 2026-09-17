"""Tests for the OpenAI backend, driven through the one SDK method it calls."""

import base64
import io
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import openai as sdk
import pytest
from openai.types.responses import Response
from PIL import Image

from docmatch.extraction import openai
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import Document, ExtractionError
from docmatch.extraction.gemini import INSTRUCTION
from docmatch.extraction.openai import KEY_VARIABLE, TIMEOUT, OpenAIExtractor


def a_document(tmp_path: Path, *, pages: int = 1) -> Document:
    """A public copy of blank US Letter pages, as the run hands one over."""
    copy = write_pdf(tmp_path / "syn0001.pdf", pages=pages)
    return Document("syn0001", copy, pages)


@pytest.fixture
def document(tmp_path: Path) -> Document:
    return a_document(tmp_path)


def answer(
    text: str | None = "{}",
    *,
    status: str = "completed",
    refusal: str | None = None,
    tokens: int = 10,
    cached: int = 0,
    written: int = 0,
    output: int = 4,
    model: str = "gpt-5.6-luna",
    incomplete: str | None = None,
) -> Response:
    """A response carrying one message, as the API returns one."""
    content: list[dict[str, Any]] = []
    if text is not None:
        content.append({"type": "output_text", "text": text, "annotations": []})
    if refusal is not None:
        content.append({"type": "refusal", "refusal": refusal})
    return Response.model_validate(
        {
            "id": "resp_1",
            "created_at": 0,
            "model": model,
            "object": "response",
            "status": status,
            "incomplete_details": {"reason": incomplete} if incomplete else None,
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": content,
                }
            ],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": tokens,
                "input_tokens_details": {
                    "cached_tokens": cached,
                    "cache_write_tokens": written,
                },
                "output_tokens": output,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": tokens + output,
            },
        }
    )


def refused(status: int) -> sdk.APIStatusError:
    """The error the SDK raises for a 4xx or 5xx answer."""
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return sdk.APIStatusError(
        f"status {status}",
        response=httpx2.Response(status, request=request),
        body=None,
    )


@dataclass
class FakeResponses:
    """Returns a prepared response and remembers what it was asked."""

    answers: list[object]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(
        self, *, model: str, input: list[dict[str, Any]], text: dict[str, Any]
    ) -> Response:
        self.calls.append({"model": model, "input": input, "text": text})
        given = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(given, Exception):
            raise given
        assert isinstance(given, Response)
        return given


def extractor(*answers: object) -> tuple[OpenAIExtractor, FakeResponses]:
    responses = FakeResponses(answers=list(answers))
    return OpenAIExtractor(responses=responses), responses


def test_reads_a_document_into_a_prediction(document: Document) -> None:
    reading, _ = extractor(answer('{"vendor_name": ["Northwind Trading Ltd"]}'))

    extracted = reading.extract(document)

    assert extracted.prediction.fields == {"vendor_name": ["Northwind Trading Ltd"]}
    assert extracted.usage.input_tokens == 10
    assert extracted.usage.output_tokens == 4
    assert extracted.confidence is None
    assert extracted.latency >= 0


def test_records_the_model_the_response_names(document: Document) -> None:
    reading, _ = extractor(answer(model="gpt-5.6-luna-2026-08-01"))

    extracted = reading.extract(document)

    assert reading.model == "gpt-5.6-luna"
    assert extracted.served_model == "gpt-5.6-luna-2026-08-01"


def test_prices_input_output_and_cached_input_at_their_own_rates(
    document: Document,
) -> None:
    """Cached tokens are part of the input count and billed at the cached rate."""
    reading, _ = extractor(answer(tokens=1_000_000, cached=400_000, output=1_000_000))

    extracted = reading.extract(document)

    assert extracted.usage.cached_input_tokens == 400_000
    assert extracted.cost == Decimal("0.12") + Decimal("0.008") + Decimal("1.20")


def test_prices_cache_writes_at_their_own_rate(document: Document) -> None:
    """The pricing page lists cache writes at $0.25, beside the three #36 named."""
    reading, _ = extractor(answer(tokens=1_000_000, written=1_000_000, output=0))

    extracted = reading.extract(document)

    assert extracted.usage.cache_write_tokens == 1_000_000
    assert extracted.cost == Decimal("0.25")


def test_asks_for_the_invoice_schema_strictly(document: Document) -> None:
    """Strict mode wants every property required and no extra keys, at every level."""
    reading, calls = extractor(answer())

    reading.extract(document)

    (call,) = calls.calls
    fmt = call["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    row = schema["$defs"]["LineItem"]
    assert row["additionalProperties"] is False
    assert set(row["required"]) == set(row["properties"])


def test_sends_the_gemini_instruction_before_numbered_pages(tmp_path: Path) -> None:
    """The two vision LLM rows differ by provider and model only (#36)."""
    reading, calls = extractor(answer())

    reading.extract(a_document(tmp_path, pages=2))

    (call,) = calls.calls
    (message,) = call["input"]
    kinds = [part["type"] for part in message["content"]]
    assert kinds == [
        "input_text",
        "input_text",
        "input_image",
        "input_text",
        "input_image",
    ]
    assert message["content"][0]["text"] == INSTRUCTION
    assert message["content"][1]["text"] == "Page 1 of 2."
    assert message["content"][3]["text"] == "Page 2 of 2."


def test_sends_pages_rendered_at_1600_px(tmp_path: Path) -> None:
    reading, calls = extractor(answer())

    reading.extract(a_document(tmp_path, pages=1))

    (call,) = calls.calls
    image = call["input"][0]["content"][2]
    header, data = image["image_url"].split(",", 1)
    assert header == "data:image/png;base64"
    assert Image.open(io.BytesIO(base64.b64decode(data))).size == (1237, 1600)


def test_an_answer_that_does_not_fit_carries_its_cost(document: Document) -> None:
    reading, _ = extractor(answer("not json at all", tokens=1_000_000))

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert "does not fit the schema" in str(raised.value)
    assert raised.value.retryable
    assert raised.value.cost == Decimal("0.20") + 4 * Decimal("1.20") / 1_000_000
    assert raised.value.usage.input_tokens == 1_000_000


def test_a_refusal_carries_its_cost_and_is_not_retried(document: Document) -> None:
    """Retries are for transport and schema errors, not for shopping an answer."""
    reading, _ = extractor(
        answer(None, refusal="I cannot help with that.", tokens=1_000_000)
    )

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert "refused" in str(raised.value)
    assert not raised.value.retryable
    assert raised.value.cost == Decimal("0.20") + 4 * Decimal("1.20") / 1_000_000


def test_an_incomplete_response_says_why_and_carries_its_cost(
    document: Document,
) -> None:
    reading, _ = extractor(
        answer('{"vendor', status="incomplete", incomplete="max_output_tokens")
    )

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert "incomplete" in str(raised.value)
    assert "max_output_tokens" in str(raised.value)
    assert raised.value.cost > 0


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_retry_a_request_the_provider_refused(
    status: int, document: Document
) -> None:
    reading, _ = extractor(refused(status))

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert not raised.value.retryable
    assert raised.value.cost == Decimal(0)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_the_providers_own_failures(status: int, document: Document) -> None:
    reading, _ = extractor(refused(status))

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert raised.value.retryable


def test_retries_a_dropped_connection(document: Document) -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    reading, _ = extractor(sdk.APIConnectionError(request=request))

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert raised.value.retryable
    assert raised.value.cost == Decimal(0)


def test_refuses_an_unpriced_model_before_calling(document: Document) -> None:
    responses = FakeResponses(answers=[answer()])
    reading = OpenAIExtractor(responses=responses, model="gpt-7-imaginary")

    with pytest.raises(ExtractionError) as raised:
        reading.extract(document)

    assert "no price is written down" in str(raised.value)
    assert not raised.value.retryable
    assert responses.calls == []


def test_refuses_an_unpriced_model_before_making_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")

    def never(**arguments: object) -> object:
        raise AssertionError("a client was made for a model that cannot be read")

    monkeypatch.setattr(sdk, "OpenAI", never)

    with pytest.raises(ExtractionError, match="no price is written down"):
        openai.extractor("gpt-7-imaginary")


def test_a_missing_key_is_named_in_the_engines_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(KEY_VARIABLE, raising=False)

    with pytest.raises(ExtractionError) as raised:
        openai.extractor()

    assert f"no ${KEY_VARIABLE} in the environment" in str(raised.value)
    assert ".env.example" in str(raised.value)


def test_gives_a_request_a_deadline_and_leaves_retries_to_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    built: dict[str, object] = {}

    def remember(**arguments: object) -> object:
        built.update(arguments)
        return SimpleNamespace(responses=FakeResponses(answers=[]))

    monkeypatch.setattr(sdk, "OpenAI", remember)

    openai.extractor()

    assert built == {
        "api_key": "not-a-real-key",
        "timeout": TIMEOUT,
        "max_retries": 0,
    }


def test_a_copy_that_cannot_be_rendered_is_not_worth_retrying(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "syn0001.pdf"
    broken.write_bytes(b"not a PDF at all")
    reading, calls = extractor(answer())

    with pytest.raises(ExtractionError) as raised:
        reading.extract(Document("syn0001", broken, pages=1))

    assert not raised.value.retryable
    assert calls.calls == []
