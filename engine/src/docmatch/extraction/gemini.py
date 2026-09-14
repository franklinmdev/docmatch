"""The cheap vision backend: a Gemini Flash-Lite model reading page images.

This is the first backend behind `Extractor`, and the one the first row of the
README's benchmark table is measured on. It renders nothing and scores nothing:
it is given pages, it asks the model to fill in `schema.Invoice`, and it reports
the reading with the tokens, the cost and the latency it took.

What was read before this was written
-------------------------------------

`client.interactions.create` with `response_format`, from
https://ai.google.dev/gemini-api/docs/structured-output and
https://ai.google.dev/gemini-api/docs/image-understanding, both read
2026-09-14, against `google-genai` 2.23.0. The older `generate_content` call
that every tutorial still shows is documented as legacy on
https://ai.google.dev/gemini-api/docs/interactions, which is why it is not used
here.

Why not the cheapest model
--------------------------

`gemini-2.5-flash-lite` is cheaper, at $0.10 and $0.40 per million tokens
against this model's $0.25 and $1.50, and the README used to call it the cheap
default. It does not work: on the Interactions API it ignores `response_format`
entirely and answers in prose or in bounding boxes, while the 3.x Flash-Lite
models honour the schema. It does obey a schema on the legacy
`generate_content` path, so the choice was a cheaper model on an API the vendor
documents as legacy, or this one. Both facts were measured on 2026-09-14 with
the same two-field schema, and the difference over a hundred documents is
cents.

Pages, and the order they are sent in
-------------------------------------

The instruction goes before the images, which is what the image-understanding
guide asks for when text accompanies a single image. Pages follow in order,
each announced by number, because a two-page invoice whose table continues over
the break is one table and the model has to be told which half comes first.
"""

import base64
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from google import genai

# The response class, reached through the module that defines it. `google.genai
# .interactions.Interaction` is the same class at runtime, but the SDK has two
# things by that name, a resource class and a `TypeAliasType` for the request
# union, and which one wins depends on import order. A typechecker resolves the
# alias and then denies that a response carries `output_text`. google-genai
# 2.23.0; worth re-checking whenever the SDK is upgraded.
from google.genai._gaos.types.interactions.interaction import Interaction
from pydantic import ValidationError

from docmatch.extraction.extractor import Extraction, ExtractionError, Price, Usage
from docmatch.extraction.pages import PageImage
from docmatch.extraction.schema import Invoice

MODEL = "gemini-3.1-flash-lite"
"""The cheapest Gemini model that honours a response schema on this API."""

PRICES = {
    "gemini-3.1-flash-lite": Price(
        input_per_million=Decimal("0.25"),
        output_per_million=Decimal("1.50"),
        read="2026-09-14",
    ),
    "gemini-3.5-flash-lite": Price(
        input_per_million=Decimal("0.30"),
        output_per_million=Decimal("2.50"),
        read="2026-09-14",
    ),
    "gemini-2.5-flash-lite": Price(
        input_per_million=Decimal("0.10"),
        output_per_million=Decimal("0.40"),
        read="2026-09-14",
    ),
}
"""Paid-tier list prices per million tokens, from the vendor's pricing page.

https://ai.google.dev/gemini-api/docs/pricing. The free tier is $0 and is not
what a benchmark row should quote: a number that says a run cost nothing says
nothing about what the backend costs.
"""

INSTRUCTION = """\
You are reading one invoice or receipt, given as one image per page in order.

Fill in the schema with what the document actually shows.

- Copy values exactly as printed, including the currency symbol, the thousands \
separator and the date's own format. Do not convert, reformat or compute \
anything.
- A field the document does not show is an empty list, and a cell the row does \
not show is null. Never guess a value and never carry one over from another \
field.
- A header field that the document prints in more than one place, such as the \
vendor's name in the letterhead and again in the footer, is one value, listed \
once. List a second value only when the document really shows a different one.
- Give one line_items entry per printed row of the line-item table, in the \
order printed, and let a table that continues on the next page continue in the \
same list. Totals, subtotals and tax-summary lines are not rows.
"""


KEY_VARIABLE = "GEMINI_API_KEY"


def client() -> genai.Client:
    """A client for the key in the environment, or a message saying there is none.

    The SDK finds the key itself, but the error it raises when there is none
    reads like a library problem. A run that cannot start because a key is
    missing should say so in the words of this engine.
    """
    key = os.environ.get(KEY_VARIABLE)
    if not key:
        raise ExtractionError(
            f"no ${KEY_VARIABLE} in the environment. The run needs one; put it "
            "in .env beside DOCILE_TOKEN, as .env.example shows."
        )
    return genai.Client(api_key=key)


def extractor(model: str = MODEL) -> "GeminiExtractor":
    """A backend reading with the key in the environment, and its client kept."""
    keep = client()
    return GeminiExtractor(interactions=keep.interactions, model=model, owner=keep)


def _content(pages: Sequence[PageImage]) -> list[dict[str, Any]]:
    """The instruction, then every page announced and shown."""
    content: list[dict[str, Any]] = [{"type": "text", "text": INSTRUCTION}]
    for page in pages:
        content.append({"type": "text", "text": f"Page {page.number} of {len(pages)}."})
        content.append(
            {
                "type": "image",
                "data": base64.b64encode(page.png).decode("ascii"),
                "mime_type": "image/png",
            }
        )
    return content


class Interactions(Protocol):
    """The one SDK method this backend calls, named as the SDK names it.

    Depending on the method rather than on the client is what lets a test drive
    the backend without a key and without a network: `genai.Client().interactions`
    satisfies this, and so does a stub that returns a prepared answer. The
    return is `object` because the SDK's own annotation is a union with a
    stream, and this backend narrows it before touching anything.
    """

    def create(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> object: ...


@dataclass(frozen=True)
class GeminiExtractor:
    """Reads pages with one Gemini model, over the Interactions API."""

    interactions: Interactions
    model: str = MODEL
    schema: dict[str, Any] = field(default_factory=Invoice.model_json_schema)
    owner: object = None
    """Whatever `interactions` came off, kept only so that it outlives this.

    `genai.Client().interactions` does not keep its client alive, and a client
    that is collected closes its connection pool, so an extractor built from a
    temporary client fails every document with "Cannot send a request, as the
    client has been closed". That is what the first end-to-end run did. Use
    `extractor()` rather than filling this in by hand.
    """

    @property
    def name(self) -> str:
        return self.model

    @property
    def price(self) -> Price:
        try:
            return PRICES[self.model]
        except KeyError:
            raise ExtractionError(
                f"no price is written down for {self.model}: add it to "
                "extraction/gemini.py with the date it was read"
            ) from None

    def extract(self, pages: Sequence[PageImage]) -> Extraction:
        """Read one document, or say why it could not be read."""
        if not pages:
            raise ExtractionError("a document with no pages cannot be read")
        started = time.perf_counter()
        try:
            answer = self.interactions.create(
                model=self.model,
                input=_content(pages),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": self.schema,
                },
            )
        except Exception as error:
            raise ExtractionError(f"{type(error).__name__}: {error}") from error
        latency = time.perf_counter() - started
        if not isinstance(answer, Interaction):
            raise ExtractionError(
                "the backend streamed its answer, which this extractor did not ask for"
            )
        return Extraction(
            prediction=_read(answer).prediction(),
            usage=_usage(answer),
            cost=self.price.of(_usage(answer)),
            latency=latency,
        )


def _read(answer: Interaction) -> Invoice:
    """The model's JSON as an `Invoice`, or the reason it is not one."""
    if answer.status != "completed":
        raise ExtractionError(f"the backend returned status {answer.status!r}")
    text = answer.output_text
    if not text:
        raise ExtractionError("the backend returned no text")
    try:
        return Invoice.model_validate_json(text)
    except ValidationError as error:
        raise ExtractionError(
            f"the backend's answer does not fit the schema: {error.error_count()} "
            f"problems, the first at {_where(error)}"
        ) from error


def _where(error: ValidationError) -> str:
    """Where in the answer the first problem is, named without quoting it."""
    location = error.errors()[0]["loc"]
    return ".".join(str(part) for part in location) or "the answer itself"


def _usage(answer: Interaction) -> Usage:
    """What the call was billed for.

    A missing count is a zero rather than an error. The cost of a document is
    reported beside its score, so a backend that stopped reporting tokens
    should show up as a suspiciously cheap row that a human can chase, not as a
    hundred documents that failed to extract.
    """
    usage = answer.usage
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(
        input_tokens=usage.total_input_tokens or 0,
        output_tokens=usage.total_output_tokens or 0,
    )
