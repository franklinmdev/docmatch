"""Choosing a backend and the model it reads with, by name.

`docmatch extract --backend` names a vendor and `--model` one of its models.
Each backend owns its default model and its price table, in its own module and
never in an environment variable (#36), so swapping the model a row is measured
on is a change to that default and a price entry beside it, and nothing here.
A model with no written price is refused by the backend before any request.

Gemini is the only backend wired so far. Azure's prebuilt invoice model and the
OpenAI row are named so the command offers the three rows the benchmark will
carry, and refused until their own modules exist.
"""

from docmatch.extraction import gemini
from docmatch.extraction.extractor import ExtractionError, Extractor

BACKENDS = ("gemini", "azure", "openai")


def extractor(backend: str, model: str | None, *, long_edge: int) -> Extractor:
    """The named backend reading with `model`, or with its own default when None."""
    if backend == "gemini":
        return gemini.extractor(model or gemini.MODEL, long_edge=long_edge)
    raise ExtractionError(
        f"the {backend} backend is not wired yet; gemini is the only backend "
        "that reads so far",
        retryable=False,
    )
