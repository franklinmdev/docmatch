"""Tests for the Gemini backend, driven through the one SDK method it calls."""

from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from google import genai
from google.genai._gaos.types.interactions.interaction import Interaction
from google.genai.types import HttpOptions

from docmatch.extraction import gemini
from docmatch.extraction.extractor import ExtractionError
from docmatch.extraction.gemini import (
    INSTRUCTION,
    KEY_VARIABLE,
    TIMEOUT,
    GeminiExtractor,
)
from docmatch.extraction.pages import MIME_TYPE, PageImage

PAGE = PageImage(number=1, png=b"\x89PNG\r\n\x1a\nfirst", width=1237, height=1600)
SECOND = PageImage(number=2, png=b"\x89PNG\r\n\x1a\nsecond", width=1237, height=1600)


def answer(
    text: str, *, status: str = "completed", tokens: int = 10, thoughts: int = 0
) -> Interaction:
    """An interaction carrying one text output, as the API returns one."""
    return Interaction.model_validate(
        {
            "status": status,
            "steps": [
                {"type": "model_output", "content": [{"type": "text", "text": text}]}
            ],
            "usage": {
                "total_input_tokens": tokens,
                "total_output_tokens": 4,
                "total_thought_tokens": thoughts,
                "total_tokens": tokens + 4 + thoughts,
            },
        }
    )


class Refused(Exception):
    """An SDK error carrying the provider's status, as the SDK's own do."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@dataclass
class FakeInteractions:
    """Returns a prepared answer and remembers what it was asked."""

    answers: list[object]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> object:
        self.calls.append(
            {"model": model, "input": input, "response_format": response_format}
        )
        given = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(given, Exception):
            raise given
        return given


def extractor(*answers: object) -> tuple[GeminiExtractor, FakeInteractions]:
    interactions = FakeInteractions(answers=list(answers))
    return GeminiExtractor(interactions=interactions), interactions


def test_reads_a_document_into_a_prediction() -> None:
    reading, _ = extractor(answer('{"vendor_name": ["Northwind Trading Ltd"]}'))

    extracted = reading.extract([PAGE])

    assert extracted.prediction.fields == {"vendor_name": ["Northwind Trading Ltd"]}
    assert extracted.usage.input_tokens == 10
    assert extracted.usage.output_tokens == 4
    assert extracted.latency >= 0


def test_prices_the_call_at_the_rate_written_down_for_the_model() -> None:
    reading, _ = extractor(answer("{}", tokens=1_000_000))

    extracted = reading.extract([PAGE])

    assert extracted.cost == Decimal("0.25") + 4 * Decimal("1.50") / 1_000_000


def test_asks_for_the_schema_by_name() -> None:
    reading, calls = extractor(answer("{}"))

    reading.extract([PAGE])

    (call,) = calls.calls
    assert call["response_format"]["mime_type"] == "application/json"
    assert "vendor_name" in call["response_format"]["schema"]["properties"]


def test_puts_the_instruction_before_the_pages_and_numbers_them() -> None:
    """The guide asks for the text before the image, and a table can span pages.

    A two-page invoice whose line items continue over the break is one table,
    and the model is only able to keep it in order if it is told which half it
    is looking at.
    """
    reading, calls = extractor(answer("{}"))

    reading.extract([PAGE, SECOND])

    (call,) = calls.calls
    kinds = [part["type"] for part in call["input"]]
    assert kinds == ["text", "text", "image", "text", "image"]
    assert call["input"][0]["text"] == INSTRUCTION
    assert call["input"][1]["text"] == "Page 1 of 2."
    assert call["input"][3]["text"] == "Page 2 of 2."


def test_refuses_a_document_with_no_pages() -> None:
    reading, _ = extractor(answer("{}"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([])

    assert "no pages" in str(raised.value)


def test_reports_a_status_that_is_not_completed() -> None:
    reading, _ = extractor(answer("{}", status="failed"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "failed" in str(raised.value)


def test_reports_an_answer_that_is_not_json() -> None:
    reading, _ = extractor(answer("I could not read this invoice, sorry."))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "does not fit the schema" in str(raised.value)


def test_reports_an_answer_with_nothing_in_it() -> None:
    reading, _ = extractor(answer(""))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "no text" in str(raised.value)


def test_names_where_an_answer_stopped_fitting_the_schema() -> None:
    reading, _ = extractor(answer('{"line_items": [{"line_item_quantity": 2}]}'))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "line_items.0.line_item_quantity" in str(raised.value)


def test_turns_an_sdk_failure_into_an_extraction_failure() -> None:
    """Whatever the SDK raises, the run above sees one kind of failure.

    A run retries an `ExtractionError` and gives up on anything else, so a
    timeout that reached this backend as a library exception has to arrive
    upstairs as this one or it would end the whole run.
    """
    reading, _ = extractor(TimeoutError("the connection went away"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "TimeoutError" in str(raised.value)


def test_refuses_a_model_whose_price_is_not_written_down_before_calling() -> None:
    """A model the provider bills and PRICES lacks is never sent anything.

    Called first and refused after, every attempt would be paid for and then
    reported at nothing, with the cap never reached.
    """
    interactions = FakeInteractions(answers=[answer("{}")])
    reading = GeminiExtractor(
        interactions=interactions, model="gemini-4-flash-lite-imaginary"
    )

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "no price is written down" in str(raised.value)
    assert interactions.calls == []
    assert not raised.value.retryable


def test_an_answer_that_did_not_fit_still_carries_what_it_cost() -> None:
    """The model was paid for an answer nobody could use.

    The run adds this onto the document's total and weighs it against the cost
    cap, so a document that keeps answering unusably stops costing money.
    """
    reading, _ = extractor(answer("not json at all", tokens=1_000_000))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert raised.value.cost == Decimal("0.25") + 4 * Decimal("1.50") / 1_000_000


def test_a_call_that_never_reached_the_model_carries_no_cost() -> None:
    reading, _ = extractor(TimeoutError("the connection went away"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert raised.value.cost == Decimal(0)


def test_says_what_the_api_said_was_wrong() -> None:
    """A hundred documents carrying only `status 'failed'` cannot be diagnosed."""
    unusable = Interaction.model_validate(
        {"status": "failed", "errors": [{"message": "quota exceeded"}]}
    )
    reading, _ = extractor(unusable)

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "quota exceeded" in str(raised.value)


def test_refuses_pages_too_heavy_to_send_inline() -> None:
    """Named here rather than retried twice against a provider rejection."""
    heavy = PageImage(number=1, png=b"\x00" * 16_000_000, width=1237, height=1600)
    reading, _ = extractor(answer("{}"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([heavy])

    assert "20MB" in str(raised.value)


def test_names_the_page_format_once() -> None:
    reading, calls = extractor(answer("{}"))

    reading.extract([PAGE])

    (call,) = calls.calls
    assert call["input"][2]["mime_type"] == MIME_TYPE


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_ask_for_a_retry_of_a_request_the_provider_refused(
    status: int,
) -> None:
    """A bad key or a malformed body is refused again on the next attempt.

    Retried, a revoked key would cost every document three requests and six
    seconds of backoff, to produce nothing.
    """
    reading, _ = extractor(Refused(status))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert not raised.value.retryable


@pytest.mark.parametrize(
    "failure", [Refused(429), Refused(503), TimeoutError("the connection went away")]
)
def test_asks_for_a_retry_of_the_providers_own_failures(failure: Exception) -> None:
    reading, _ = extractor(failure)

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert raised.value.retryable


def test_a_document_with_no_pages_is_not_worth_retrying() -> None:
    reading, _ = extractor(answer("{}"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([])

    assert not raised.value.retryable


def test_bills_thought_tokens_at_the_output_rate() -> None:
    """The vendor prices output "including thinking tokens" and counts them apart."""
    reading, _ = extractor(answer("{}", thoughts=6))

    extracted = reading.extract([PAGE])

    assert extracted.usage.output_tokens == 10
    assert extracted.cost == (10 * Decimal("0.25") + 10 * Decimal("1.50")) / 1_000_000


def test_an_answer_that_did_not_fit_still_carries_its_tokens() -> None:
    """The tokens go where the cost goes, so the rates reproduce the money."""
    reading, _ = extractor(answer("not json at all", tokens=1_000_000))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert raised.value.usage.input_tokens == 1_000_000
    assert raised.value.usage.output_tokens == 4


def test_measures_the_request_as_it_is_sent_and_not_the_pages_alone() -> None:
    """The limit covers the prompt and the schema, not only the images.

    Pages that base64 brings to just under it still go over once the
    instruction, the page labels and the response schema are around them, and
    the provider's rejection of that is what the check exists to pre-empt.
    """
    nearly = PageImage(number=1, png=b"\x00" * 14_995_000, width=1237, height=1600)
    reading, calls = extractor(answer("{}"))

    with pytest.raises(ExtractionError) as raised:
        reading.extract([nearly])

    assert "20MB" in str(raised.value)
    assert not raised.value.retryable
    assert calls.calls == []


def test_names_an_answer_that_is_not_an_interaction() -> None:
    reading, _ = extractor(object())

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "not one interaction" in str(raised.value)
    assert not raised.value.retryable


def test_gives_a_request_a_deadline_and_leaves_retries_to_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK would otherwise wait forever and retry four times in silence."""
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")
    built: dict[str, object] = {}

    def remember(*, api_key: str, http_options: HttpOptions) -> object:
        built["api_key"] = api_key
        built["http_options"] = http_options
        return SimpleNamespace(interactions=FakeInteractions(answers=[]))

    monkeypatch.setattr(genai, "Client", remember)

    gemini.extractor()

    options = built["http_options"]
    assert isinstance(options, HttpOptions)
    assert options.timeout == TIMEOUT
    assert options.retry_options is not None
    assert options.retry_options.attempts == 1


def test_refuses_an_unpriced_model_before_making_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VARIABLE, "not-a-real-key")

    def never(**arguments: object) -> object:
        raise AssertionError("a client was made for a model that cannot be read")

    monkeypatch.setattr(genai, "Client", never)

    with pytest.raises(ExtractionError) as raised:
        gemini.extractor("gemini-4-flash-lite-imaginary")

    assert "no price is written down" in str(raised.value)
