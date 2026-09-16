"""The second vision LLM provider: an OpenAI model reading page images.

The third backend behind `Extractor` (#21, #36). It is the Gemini backend with
the provider swapped and nothing else: the same 1600 px renders, the same
instruction, the same `schema.Invoice`, pages announced the same way, reasoning
effort left at the vendor's default and no logprobs asked for (#22). So the two
vision LLM rows differ by provider and model only.

What was read before this was written
-------------------------------------

All read 2026-09-16, against `openai` 3.14.1:

- The price, standard tier, short context: $0.20 input, $0.02 cached input,
  $0.25 cache writes and $1.20 output per million tokens, on
  https://developers.openai.com/api/docs/pricing, the page
  platform.openai.com/docs/pricing now redirects to. The cached rate #36 wrote
  down is the one listed; the cache-write rate is one #36 did not name.
  https://developers.openai.com/api/docs/guides/prompt-caching says caching
  is automatic past 1,024 prompt tokens, that for GPT-5.6 "cache writes cost
  1.25x the standard, uncached input-token rate", and that input tokens are
  each charged at one of the three input rates, so both cache counts are
  parts of `input_tokens`.
- The model page, https://developers.openai.com/api/docs/models/gpt-5.6-luna,
  lists one snapshot, `gpt-5.6-luna` itself, and reasoning effort `medium` by
  default. With no dated snapshot to point at, whether a response's `model`
  ever names something other than the alias is unverified until a run shows
  one; it is recorded as the response names it either way.
- `client.responses.create` with `text.format` of type `json_schema` and
  `strict`, `input_text` and `input_image` parts, from
  https://developers.openai.com/api/docs/guides/structured-outputs and
  https://developers.openai.com/api/docs/guides/images-vision, and the SDK's
  own types. `detail` is sent as `auto`, the documented default, which sizes
  a page for this model the way `original` does: the 1600 px render as sent.
- Usage, from the SDK's `ResponseUsage` and the reasoning guide: reasoning
  tokens "are billed as output tokens" and are already inside `output_tokens`,
  and `input_tokens_details` carries `cached_tokens` and `cache_write_tokens`.
  A refusal is an output part of type `refusal`.
- `OpenAI(max_retries=0)` is one request and no retries; the SDK's default is
  two, with a 600 second timeout.

Strict structured output
------------------------

Strict mode requires every object to list all of its properties as required
and to forbid extra keys. `Invoice` forbids extra keys already but gives every
field a default, so pydantic lists none as required. `strict` below marks
every property required on the schema that is sent and drops the defaults
that no longer mean anything there; the model then writes an empty list or a
null for a field the document does not show, which is what the instruction
already asks for, and `Invoice` reads the answer unchanged.
"""

import base64
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import openai
from openai.types.responses import Response
from pydantic import ValidationError

from docmatch.extraction.extractor import (
    Document,
    Extraction,
    ExtractionError,
    Price,
    Usage,
    retryable,
)
from docmatch.extraction.gemini import INSTRUCTION
from docmatch.extraction.pages import LONG_EDGE, MIME_TYPE, PageError, PageImage, render
from docmatch.extraction.schema import Invoice

TIMEOUT = 120.0
"""Seconds one request may take, the Gemini backend's deadline.

The SDK's own is 600 seconds, which a hung connection would hold the whole
sequential run for.
"""

MODEL = "gpt-5.6-luna"
"""The OpenAI row's model (#36)."""

PRICES = {
    "gpt-5.6-luna": Price(
        input_per_million=Decimal("0.20"),
        cached_input_per_million=Decimal("0.02"),
        cache_write_per_million=Decimal("0.25"),
        output_per_million=Decimal("1.20"),
        read="2026-09-16",
    ),
}
"""Standard-tier list prices per million tokens, from the vendor's pricing page."""

KEY_VARIABLE = "OPENAI_API_KEY"

SCHEMA_NAME = "invoice"


def client() -> openai.OpenAI:
    """A client for the key in the environment, or a message saying there is none."""
    key = os.environ.get(KEY_VARIABLE)
    if not key:
        raise ExtractionError(
            f"no ${KEY_VARIABLE} in the environment. The openai backend needs "
            "one; put it in .env beside DOCILE_TOKEN, as .env.example shows.",
            retryable=False,
        )
    return openai.OpenAI(api_key=key, timeout=TIMEOUT, max_retries=0)


def extractor(model: str = MODEL, long_edge: int = LONG_EDGE) -> "OpenAIExtractor":
    """A backend reading with the key in the environment.

    A model with no price written down is refused here, before a run has a
    directory or a document, and before the environment is read.
    """
    price_of(model)
    return OpenAIExtractor(
        responses=client().responses, model=model, long_edge=long_edge
    )


def price_of(model: str) -> Price:
    """The rate written down for a model, or a refusal to read with it."""
    try:
        return PRICES[model]
    except KeyError:
        raise ExtractionError(
            f"no price is written down for {model}: add it to "
            "extraction/openai.py with the date it was read",
            retryable=False,
        ) from None


def strict(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema as strict mode takes it: every property of every object required."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default":
            continue
        if isinstance(value, dict):
            out[key] = strict(value)
        elif isinstance(value, list):
            out[key] = [
                strict(each) if isinstance(each, dict) else each for each in value
            ]
        else:
            out[key] = value
    properties = out.get("properties")
    if out.get("type") == "object" and isinstance(properties, dict):
        out["required"] = list(properties)
    return out


def _content(pages: Sequence[PageImage]) -> list[dict[str, Any]]:
    """The instruction, then every page announced and shown, as Gemini gets them."""
    content: list[dict[str, Any]] = [{"type": "input_text", "text": INSTRUCTION}]
    for page in pages:
        content.append(
            {"type": "input_text", "text": f"Page {page.number} of {len(pages)}."}
        )
        encoded = base64.b64encode(page.png).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{MIME_TYPE};base64,{encoded}",
                "detail": "auto",
            }
        )
    return content


class Responses(Protocol):
    """The one SDK method this backend calls, named as the SDK names it.

    `openai.OpenAI().responses` satisfies this, and so does a stub that returns
    a prepared response, which is what lets a test drive the backend without a
    key and without a network.
    """

    def create(self, *, model: str, input: Any, text: Any) -> Response: ...


@dataclass(frozen=True)
class OpenAIExtractor:
    """Reads a document's rendered pages with one OpenAI model, over Responses."""

    responses: Responses
    model: str = MODEL
    long_edge: int = LONG_EDGE
    """Pixels on a rendered page's longer side."""
    schema: dict[str, Any] = field(
        default_factory=lambda: strict(Invoice.model_json_schema())
    )

    def extract(self, document: Document) -> Extraction:
        """Read one document, or say why it could not be read.

        The price is looked up before anything is sent, and a copy that cannot
        be rendered is refused without a retry, as the Gemini backend does.
        """
        price = price_of(self.model)
        try:
            pages = render(document.path, self.long_edge)
        except PageError as error:
            raise ExtractionError(str(error), retryable=False) from error
        text = {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "schema": self.schema,
                "strict": True,
            }
        }
        started = time.perf_counter()
        try:
            answer = self.responses.create(
                model=self.model,
                input=[{"role": "user", "content": _content(pages)}],
                text=text,
            )
        except Exception as error:
            raise ExtractionError(
                f"{type(error).__name__}: {error}", retryable=retryable(error)
            ) from error
        latency = time.perf_counter() - started
        usage = _usage(answer)
        cost = price.of(usage)
        return Extraction(
            prediction=_read(answer, cost, usage).prediction(),
            usage=usage,
            cost=cost,
            latency=latency,
            served_model=answer.model,
        )


def _read(answer: Response, cost: Decimal, usage: Usage) -> Invoice:
    """The model's JSON as an `Invoice`, or the reason it is not one.

    Every failure here was paid for, so each carries the cost and the tokens.
    A refusal is not retried: retries are for transport and schema errors, and
    asking again until the model agrees would be shopping for an answer.
    """
    refusals = [
        part.refusal
        for item in answer.output
        if item.type == "message"
        for part in item.content
        if part.type == "refusal"
    ]
    if refusals:
        raise ExtractionError(
            f"the backend refused: {refusals[0]}", cost, usage=usage, retryable=False
        )
    if answer.status != "completed":
        raise ExtractionError(
            f"the backend returned status {answer.status!r}{_because(answer)}",
            cost,
            usage=usage,
        )
    text = answer.output_text
    if not text:
        raise ExtractionError("the backend returned no text", cost, usage=usage)
    try:
        return Invoice.model_validate_json(text)
    except ValidationError as error:
        location = error.errors()[0]["loc"]
        where = ".".join(str(part) for part in location) or "the answer itself"
        raise ExtractionError(
            f"the backend's answer does not fit the schema: {error.error_count()} "
            f"problems, the first at {where}",
            cost,
            usage=usage,
        ) from error


def _because(answer: Response) -> str:
    """What the API said was wrong, when it said anything."""
    if answer.incomplete_details and answer.incomplete_details.reason:
        return f": {answer.incomplete_details.reason}"
    if answer.error:
        return f": {answer.error.code}: {answer.error.message}"
    return ""


def _usage(answer: Response) -> Usage:
    """What the call was billed for, in the units the response reports.

    Reasoning tokens are inside `output_tokens` already and are not added
    again. A response with no usage is zero rather than an error, so a backend
    that stops reporting shows up as a suspiciously cheap row.
    """
    usage = answer.usage
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.input_tokens_details.cached_tokens,
        cache_write_tokens=usage.input_tokens_details.cache_write_tokens,
        output_tokens=usage.output_tokens,
    )
