"""Choosing a backend and the model it reads with, by name.

`docmatch extract --backend` names a vendor and `--model` one of its models.
Each backend owns its default model and its price table, in its own module and
never in an environment variable (#36), so swapping the model a row is measured
on is a change to that default and a price entry beside it, and nothing here.
A model with no written price is refused by the backend before any request.

Gemini, Azure's prebuilt invoice model and OpenAI are the three rows the
benchmark carries. Azure reads the PDF itself, so `long_edge` means nothing to
it.
"""

from docmatch.extraction import azure, gemini, openai
from docmatch.extraction.extractor import ExtractionError, Extractor
from docmatch.extraction.schema import Invoice

BACKENDS = ("gemini", "azure", "openai")

RENDERING = ("gemini", "openai")
"""The backends that render pages at `--long-edge`; the rest read the PDF itself."""


def extractor(
    backend: str, model: str | None, *, long_edge: int, asks: type[Invoice] = Invoice
) -> Extractor:
    """The named backend reading with `model`, or with its own default when None,
    asked to fill in `asks`. Azure's prebuilt model has a fixed schema of its
    own, so it ignores `asks`."""
    if backend == "gemini":
        return gemini.extractor(model or gemini.MODEL, long_edge=long_edge, asks=asks)
    if backend == "azure":
        return azure.extractor(model or azure.MODEL)
    if backend == "openai":
        return openai.extractor(model or openai.MODEL, long_edge=long_edge, asks=asks)
    raise ExtractionError(
        f"no backend named {backend}; the backends are {', '.join(BACKENDS)}",
        retryable=False,
    )
