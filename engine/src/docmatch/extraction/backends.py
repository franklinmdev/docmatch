"""Choosing a backend and the model it reads with, by name.

`docmatch extract --backend` names a vendor and `--model` one of its models.
Each backend owns its default model and its price table, in its own module and
never in an environment variable (#36), so swapping the model a row is measured
on is a change to that default and a price entry beside it, and nothing here.
A model with no written price is refused by the backend before any request.

Gemini and Azure's prebuilt invoice model are wired. The OpenAI row is named so
the command offers the three rows the benchmark will carry, and refused until
its own module exists. Azure reads the PDF itself, so `long_edge` means nothing
to it.
"""

from docmatch.extraction import azure, gemini
from docmatch.extraction.extractor import ExtractionError, Extractor

BACKENDS = ("gemini", "azure", "openai")


def extractor(backend: str, model: str | None, *, long_edge: int) -> Extractor:
    """The named backend reading with `model`, or with its own default when None."""
    if backend == "gemini":
        return gemini.extractor(model or gemini.MODEL, long_edge=long_edge)
    if backend == "azure":
        return azure.extractor(model or azure.MODEL)
    raise ExtractionError(
        f"the {backend} backend is not wired yet; gemini and azure are the "
        "backends that read so far",
        retryable=False,
    )
