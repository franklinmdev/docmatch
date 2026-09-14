"""Tests for the Gemini backend, driven through the one SDK method it calls."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from google.genai._gaos.types.interactions.interaction import Interaction

from docmatch.extraction.extractor import ExtractionError
from docmatch.extraction.gemini import INSTRUCTION, GeminiExtractor
from docmatch.extraction.pages import PageImage

PAGE = PageImage(number=1, png=b"\x89PNG\r\n\x1a\nfirst", width=1237, height=1600)
SECOND = PageImage(number=2, png=b"\x89PNG\r\n\x1a\nsecond", width=1237, height=1600)


def answer(text: str, *, status: str = "completed", tokens: int = 10) -> Interaction:
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
                "total_tokens": tokens + 4,
            },
        }
    )


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


def test_refuses_a_model_whose_price_is_not_written_down() -> None:
    reading = GeminiExtractor(
        interactions=FakeInteractions(answers=[answer("{}")]),
        model="gemini-4-flash-lite-imaginary",
    )

    with pytest.raises(ExtractionError) as raised:
        reading.extract([PAGE])

    assert "no price is written down" in str(raised.value)
