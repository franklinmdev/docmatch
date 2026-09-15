"""The cheap vision backend: a Gemini Flash-Lite model reading page images.

This is the first backend behind `Extractor`, and the one the first row of the
README's benchmark table is measured on. It scores nothing: it is given a
document, renders the pages of its public copy, asks the model to fill in
`schema.Invoice`, and reports the reading with the tokens, the cost and the
latency it took.

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

Every page is rendered by `pages.render` at `long_edge`, 1600 px by default,
colour, PNG, with the page's `/Rotate` applied, and with neither the archive's
footer cropped nor the scan deskewed (#34). That is the render the Phase 0 row
was measured on, moved behind the backend so a backend that reads the PDF
itself never renders anything.

The instruction goes before the images, which is what the image-understanding
guide asks for when text accompanies a single image. Pages follow in order,
each announced by number, because a two-page invoice whose table continues over
the break is one table and the model has to be told which half comes first.
"""

import base64
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai.types import HttpOptions, HttpRetryOptions
from pydantic import ValidationError

from docmatch.extraction.extractor import (
    Document,
    Extraction,
    ExtractionError,
    Price,
    Usage,
)
from docmatch.extraction.pages import LONG_EDGE, MIME_TYPE, PageError, PageImage, render
from docmatch.extraction.schema import Invoice

INLINE_LIMIT = 20_000_000
"""Bytes a request may carry inline, the whole of it, prompt included.

https://ai.google.dev/gemini-api/docs/image-understanding, read 2026-09-14,
which bounds "text prompts, system instructions, and inline bytes" together and
does not say whether its 20MB is decimal or binary, so this is the smaller
reading. The check measures the request body as it will be sent, base64 pages,
instruction, page labels and the response schema, because a document over the
limit is a failure this backend names, rather than a provider rejection retried
twice with a message the run report cannot explain.
"""

TIMEOUT = 120_000
"""Milliseconds one request may take before it is given up on.

`HttpOptions.timeout`, google-genai 2.23.0, which the SDK leaves unset, and an
unset timeout is none at all: a server that accepts the connection and never
answers would hold the whole sequential run. Ten times the slowest document of
the first run, so it ends a hang and not a slow page.
"""

RETRIES = HttpRetryOptions(attempts=1)
"""No retries inside the SDK; the run above does its own, against the cap.

The SDK retries 408, 409, 429, 5xx and dropped connections on its own, four
requests per call by default, with its sleeps landing inside what `extract`
times as the latency of one attempt. `attempts=1` is what the SDK documents as
"no retries" (`HttpRetryOptions`, google-genai 2.23.0). Measured on that
version, the Interactions path still sends one retry on this setting, half a
second later; that is the least the option allows, and worth re-measuring on
an upgrade.
"""

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
    return genai.Client(
        api_key=key, http_options=HttpOptions(timeout=TIMEOUT, retry_options=RETRIES)
    )


def extractor(model: str = MODEL, long_edge: int = LONG_EDGE) -> "GeminiExtractor":
    """A backend reading with the key in the environment, and its client kept.

    A model with no price written down is refused here, before a run has a
    directory or a document, rather than after the provider has been paid.
    """
    price_of(model)
    keep = client()
    return GeminiExtractor(
        interactions=keep.interactions, model=model, long_edge=long_edge, owner=keep
    )


def price_of(model: str) -> Price:
    """The rate written down for a model, or a refusal to read with it."""
    try:
        return PRICES[model]
    except KeyError:
        raise ExtractionError(
            f"no price is written down for {model}: add it to "
            "extraction/gemini.py with the date it was read",
            retryable=False,
        ) from None


def _content(pages: Sequence[PageImage]) -> list[dict[str, Any]]:
    """The instruction, then every page announced and shown."""
    content: list[dict[str, Any]] = [{"type": "text", "text": INSTRUCTION}]
    for page in pages:
        content.append({"type": "text", "text": f"Page {page.number} of {len(pages)}."})
        content.append(
            {
                "type": "image",
                "data": base64.b64encode(page.png).decode("ascii"),
                "mime_type": MIME_TYPE,
            }
        )
    return content


class Interactions(Protocol):
    """The one SDK method this backend calls, named as the SDK names it.

    Depending on the method rather than on the client is what lets a test drive
    the backend without a key and without a network: `genai.Client().interactions`
    satisfies this, and so does a stub that returns a prepared answer. The
    return is `object` because the SDK's own annotation is a union with a
    stream, and this backend narrows it to `Answer` before touching anything.
    """

    def create(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],
        response_format: dict[str, Any],
    ) -> object: ...


@runtime_checkable
class Answer(Protocol):
    """What this backend reads off one interaction, and nothing else.

    Named here rather than imported, because the SDK keeps its response class
    in a private module and has two public things called `Interaction`, a
    resource class and a request union, and which one a typechecker sees
    depends on import order. Four attributes are all that is read, so four
    attributes are what an answer has to carry, whichever class it is; an SDK
    release that renames one fails the narrowing below with a message that
    says so, rather than failing every `docmatch` command at import.
    """

    @property
    def status(self) -> str: ...

    @property
    def output_text(self) -> str | None: ...

    @property
    def usage(self) -> "Counted | None": ...

    @property
    def errors(self) -> Sequence[object] | None: ...


class Counted(Protocol):
    """The token counts an interaction's usage carries."""

    @property
    def total_input_tokens(self) -> int | None: ...

    @property
    def total_output_tokens(self) -> int | None: ...

    @property
    def total_thought_tokens(self) -> int | None: ...


@dataclass(frozen=True)
class GeminiExtractor:
    """Reads a document's rendered pages with one Gemini model, over Interactions."""

    interactions: Interactions
    model: str = MODEL
    long_edge: int = LONG_EDGE
    """Pixels on a rendered page's longer side."""
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
        return price_of(self.model)

    def extract(self, document: Document) -> Extraction:
        """Read one document, or say why it could not be read.

        Everything that can be refused is refused before the call: the price
        is looked up first, because a model the provider bills and this module
        does not price would otherwise be paid for and then reported at
        nothing, once per attempt, with the cap never reached. A copy that
        cannot be rendered is refused next, and not retried: the same bytes
        fail the same way.
        """
        price = self.price
        try:
            pages = render(document.path, self.long_edge)
        except PageError as error:
            raise ExtractionError(str(error), retryable=False) from error
        content = _content(pages)
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": self.schema,
        }
        inline = len(
            json.dumps(
                {
                    "model": self.model,
                    "input": content,
                    "response_format": response_format,
                }
            )
        )
        if inline > INLINE_LIMIT:
            raise ExtractionError(
                f"{len(pages)} pages come to {inline / 1_000_000:.1f}MB once "
                f"base64 has widened them, over the {INLINE_LIMIT // 1_000_000}MB "
                "a request may carry inline. Render smaller, or send the pages "
                "through the Files API.",
                retryable=False,
            )
        started = time.perf_counter()
        try:
            answer = self.interactions.create(
                model=self.model, input=content, response_format=response_format
            )
        except Exception as error:
            raise ExtractionError(
                f"{type(error).__name__}: {error}", retryable=_retryable(error)
            ) from error
        latency = time.perf_counter() - started
        if not isinstance(answer, Answer):
            raise ExtractionError(
                f"the backend answered with {type(answer).__name__} and not one "
                "interaction. Either it streamed, which this extractor does not "
                "ask for, or the SDK renamed what an interaction carries; "
                "`Answer` above says what is read.",
                retryable=False,
            )
        usage = _usage(answer)
        cost = price.of(usage)
        return Extraction(
            prediction=_read(answer, cost, usage).prediction(),
            usage=usage,
            cost=cost,
            latency=latency,
        )


def _retryable(error: Exception) -> bool:
    """Whether the same request could succeed next time.

    An HTTP error from the SDK carries its status. A rate limit or a server
    error is the provider's moment and not this request's; anything else in
    the 4xx range is a request the provider will refuse again, a bad key, a
    model it does not serve or a body it does not accept. An exception with
    no status never reached an answer, which a retry may fix.
    """
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        return True
    return status in (408, 409, 429) or status >= 500


def _read(answer: Answer, cost: Decimal, usage: Usage) -> Invoice:
    """The model's JSON as an `Invoice`, or the reason it is not one.

    Every failure here is one the model was paid for, so each carries the cost
    and the tokens out to the run that has to add them up.
    """
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
        raise ExtractionError(
            f"the backend's answer does not fit the schema: {error.error_count()} "
            f"problems, the first at {_where(error)}",
            cost,
            usage=usage,
        ) from error


def _because(answer: Answer) -> str:
    """What the API said was wrong, when it said anything.

    A status on its own is not actionable, and a hundred documents carrying
    `status 'failed'` and nothing else is a run nobody can diagnose. The reason,
    a safety block or a quota or a malformed request, is in `errors`.
    """
    errors = answer.errors or []
    return f": {errors[0]}" if errors else ""


def _where(error: ValidationError) -> str:
    """Where in the answer the first problem is, named without quoting it."""
    location = error.errors()[0]["loc"]
    return ".".join(str(part) for part in location) or "the answer itself"


def _usage(answer: Answer) -> Usage:
    """What the call was billed for.

    Thought tokens count as output. The API reports them apart from the
    answer's own tokens, and prices them at the output rate: "response pricing
    is the sum of output tokens and thinking tokens" on
    https://ai.google.dev/gemini-api/docs/thinking, and the pricing page lists
    the output rate as "including thinking tokens", both read 2026-09-14. The
    model reads at its default thinking level, which the vendor says is not a
    guarantee of no thinking, so leaving them out would bill a run below what
    the provider does.

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
        output_tokens=(usage.total_output_tokens or 0)
        + (usage.total_thought_tokens or 0),
    )
