"""What an upload carries beside the invoice: its purchase order and receipt.

The engine never looks a purchase order up. An AP desk would find it in its
ERP; here the upload carries the case Phase 2's generator builds, a purchase
order and a receiving record in the shape matching compares (map #146, #164).
It is read with the records' own types, so a case the matcher could not take
is refused at upload with a message, never inside the loop.

The digest
----------

A repeated upload returns the document already stored rather than a second
one (#164). What makes two uploads the same is their content: the SHA-256 over
the PDF's bytes, unchanged, then the case in canonical JSON, keys sorted and
no whitespace, as read back from its types. So the same case written with
other spacing or key order is the same upload, and the same PDF with another
case is another document.
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from docmatch.matching.records import ReceivingRecord, Record
from docmatch.metrics.fields import first_problem


@dataclass(frozen=True)
class Case:
    """The records an invoice is matched against."""

    purchase_order: Record
    receipt: ReceivingRecord


CASE = TypeAdapter(Case)


class CaseError(Exception):
    """The case part of an upload is not a case."""


def read_case(body: str | bytes) -> Case:
    """A case from its JSON, or a message saying what is wrong with it."""
    try:
        return CASE.validate_json(body)
    except ValidationError as error:
        raise CaseError(
            'the case is not a JSON object with a "purchase_order" (a "header" '
            'and "lines") and a "receipt" ("lines", each with "cells" and the '
            f'"po_line" it answers). {first_problem(error)}'
        ) from error


def case_json(case: Case) -> dict[str, object]:
    """The case as JSON values, the way it is stored and shown."""
    dumped: dict[str, object] = CASE.dump_python(case, mode="json")
    return dumped


def digest(invoice: bytes, case: Case) -> str:
    """The content's SHA-256, hex: the PDF's bytes, then the canonical case."""
    canonical = json.dumps(case_json(case), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(invoice + canonical.encode("utf-8")).hexdigest()
