"""Helpers for the eval tests: annotations carrying only what admission reads.

No dataset and no network. Public copies come from the PDFs
`extraction.conftest.write_pdf` builds, and the archive is a function the test
passes in.
"""

import json
from pathlib import Path


def annotate(
    root: Path,
    document_id: str,
    *,
    original_filename: str,
    page_sizes: list[list[int]],
    source: str = "ucsf",
) -> None:
    """A header-only annotation whose metadata is what admission reads."""
    annotations = root / "annotations"
    annotations.mkdir(parents=True, exist_ok=True)
    body = {
        "field_extractions": [],
        "line_item_extractions": [],
        "metadata": {
            "original_filename": original_filename,
            "page_count": len(page_sizes),
            "page_sizes_at_200dpi": page_sizes,
            "source": source,
        },
    }
    (annotations / f"{document_id}.json").write_text(json.dumps(body), "utf-8")
