"""The commercial baseline: Azure Document Intelligence's prebuilt invoice model.

The second backend behind `Extractor`, and the only one with a native
confidence (#21, #22). It is given a document, sends the public PDF unchanged
(#34), polls the analysis to completion, and maps what the model returns onto
DocILE's fieldtypes, keeping beside the reading the confidence of every value
and cell and the currency symbol of the two amounts the derived currency reads.

What was read before this was written
-------------------------------------

All read 2026-09-16, against `azure-ai-documentintelligence` 1.0.2 and
`azure-core` 1.41.0:

- The field list, from the 2024-11-30 (GA) invoice schema that
  https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/invoice
  links to, https://github.com/Azure-Samples/document-intelligence-code-samples/blob/main/schema/2024-11-30-ga/invoice.md.
- `content` on a field: optional. The stable 2024-11-30 swagger
  (`specification/ai/data-plane/DocumentIntelligence/stable/2024-11-30`) lists
  only `type` as required on `DocumentField`, so a field can come back with a
  typed value and no printed text. Such a field is left out, not filled from
  its typed value.
- The flow: the analyze POST answers 202 with `Operation-Location`, which is
  polled until the operation ends
  (https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document).
  The swagger's analyze operation consumes `application/pdf` and
  `application/octet-stream`, and the SDK sends a byte stream as the latter.
  `begin_analyze_document` makes the POST before it returns the poller, so a
  refused request raises there and an accepted one can only fail while it is
  polled; `LROPoller.result(timeout)` returns without raising when the timeout
  passes first, which is why `done()` is asked afterwards.
- The price: $10 per 1,000 pages for every prebuilt model on S0, pay as you go,
  https://azure.microsoft.com/en-us/pricing/details/document-intelligence/,
  Central US, USD. The same figure #35 read on 2026-09-15.

Printed text, never typed values
--------------------------------

Every mapped value is the field's `content`, what the page prints. A
`valueDate` or `valueCurrency` is Azure resolving ambiguous text, a date that
reads two ways or a `$` that could be any dollar, which rule 4 reserves for
code, and it would hand this row a normalizer the vision LLM rows do not get.
For the same reason `currency_code` is never read; `currency_symbol` on
`AmountDue` and `InvoiceTotal` is kept apart as the source of the derived
currency (#24).

The mapping
-----------

Written against the field list above. An Azure field with no DocILE
counterpart is dropped (`TotalDiscount`, `PreviousUnpaidBalance`, the service
period, `PaidInFourInstallements`, the BPay details), and a DocILE fieldtype no
Azure field feeds stays empty. Where a DocILE fieldtype has more than one
Azure source, every source's value is listed, the way a vision LLM lists every
distinct value it sees; the scorer compares distinct normalized values, so a
name printed once and returned twice counts once.

Three Azure fields could go to either of two DocILE fieldtypes. Each goes to
the one DocILE's annotators use more, counted over the train split's UCSF
documents and not over the val subset the row is measured on:
`PurchaseOrder` to `order_id` (708 documents, against 274 for
`customer_order_id`), an item's `Amount` to `line_item_amount_gross` (7,939
cells, against 1,537 net), and `UnitPrice` to `line_item_unit_price_gross`
(2,262 cells, against 844 net).

Pages, and what an attempt costs
--------------------------------

The response carries no billing field, so pages processed are the length of
`analyzeResult.pages` (#28). Fewer than were sent fails the document at once,
not retried, charged for the pages processed. An analysis that was accepted
and never reported a page count, because polling dropped, failed or ran out of
time, is charged for the pages sent and may be retried. A request refused
outright costs nothing (#35).
"""

import io
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import IO, Protocol

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentField
from azure.core.credentials import AzureKeyCredential

from docmatch.extraction.extractor import (
    Confidence,
    Document,
    Extraction,
    ExtractionError,
    Price,
    Usage,
    retryable,
)
from docmatch.metrics.fields import Prediction, WrittenValues

API_VERSION = "2024-11-30"
"""The GA API the mapping was written against, pinned rather than the SDK's default."""

MODEL = "prebuilt-invoice"

PRICES = {
    "prebuilt-invoice": Price(per_thousand_pages=Decimal("10"), read="2026-09-16"),
}
"""S0 pay-as-you-go list prices, per 1,000 pages, from the vendor's pricing page.

The F0 tier is free and processes only a document's first two pages, which is
why the row runs on S0 and quotes its price (#28).
"""

TIMEOUT = 120.0
"""Seconds an accepted analysis is polled before the attempt is given up on.

The same bound the Gemini backend puts on one request: it ends a hang, not a
slow document. The SDK keeps polling an abandoned operation on its own thread,
which is harmless to a run that has moved on.
"""

RETRIES = 0
"""No retries inside the SDK; the run above does its own, against the cap.

`retry_total`, azure-core 1.41.0, which defaults to 10 and applies to the
analyze POST and to every poll. Left on, its waits would land inside the
latency of one attempt, as they would for Gemini.
"""

ENDPOINT_VARIABLE = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"
KEY_VARIABLE = "AZURE_DOCUMENT_INTELLIGENCE_KEY"

HEADER = {
    "InvoiceId": "document_id",
    "PurchaseOrder": "order_id",
    "InvoiceDate": "date_issue",
    "DueDate": "date_due",
    "PaymentTerm": "payment_terms",
    "VendorName": "vendor_name",
    "VendorAddressRecipient": "vendor_name",
    "RemittanceAddressRecipient": "vendor_name",
    "VendorAddress": "vendor_address",
    "RemittanceAddress": "vendor_address",
    "VendorTaxId": "vendor_tax_id",
    "KVKNumber": "vendor_registration_id",
    "CustomerId": "customer_id",
    "CustomerName": "customer_billing_name",
    "CustomerAddressRecipient": "customer_billing_name",
    "BillingAddressRecipient": "customer_billing_name",
    "CustomerAddress": "customer_billing_address",
    "BillingAddress": "customer_billing_address",
    "ShippingAddressRecipient": "customer_delivery_name",
    "ShippingAddress": "customer_delivery_address",
    "ServiceAddressRecipient": "customer_other_name",
    "ServiceAddress": "customer_other_address",
    "CustomerTaxId": "customer_tax_id",
    "SubTotal": "amount_total_net",
    "TotalTax": "amount_total_tax",
    "InvoiceTotal": "amount_total_gross",
    "AmountDue": "amount_due",
}
"""Top-level prebuilt-invoice fields and the KILE fieldtype each one fills."""

HEADER_ROWS = {
    "TaxDetails": {"Amount": "tax_detail_tax", "Rate": "tax_detail_rate"},
    "PaymentDetails": {
        "IBAN": "iban",
        "SWIFT": "bic",
        "BankAccountNumber": "account_num",
    },
}
"""Array fields whose rows are header values in DocILE, not line items."""

CELLS = {
    "Description": "line_item_description",
    "Quantity": "line_item_quantity",
    "Unit": "line_item_units_of_measure",
    "UnitPrice": "line_item_unit_price_gross",
    "Amount": "line_item_amount_gross",
    "ProductCode": "line_item_code",
    "Tax": "line_item_tax",
    "TaxRate": "line_item_tax_rate",
    "Date": "line_item_date",
}
"""An `Items` row's subfields and the LIR fieldtype each one fills."""

CURRENCY_SOURCES = {"AmountDue": "amount_due", "InvoiceTotal": "amount_total_gross"}
"""The amounts whose `currency_symbol` is kept, by the fieldtype they fill."""


def client() -> DocumentIntelligenceClient:
    """A client for the endpoint and key in the environment, or what is missing."""
    found = {name: os.environ.get(name) for name in (ENDPOINT_VARIABLE, KEY_VARIABLE)}
    for name, value in found.items():
        if not value:
            raise ExtractionError(
                f"no ${name} in the environment. The azure backend needs "
                f"${ENDPOINT_VARIABLE} and ${KEY_VARIABLE}; put them in .env "
                "beside DOCILE_TOKEN, as .env.example shows."
            )
    return DocumentIntelligenceClient(
        endpoint=str(found[ENDPOINT_VARIABLE]),
        credential=AzureKeyCredential(str(found[KEY_VARIABLE])),
        api_version=API_VERSION,
        retry_total=RETRIES,
    )


def extractor(model: str = MODEL) -> "AzureExtractor":
    """A backend reading with the endpoint and key in the environment.

    A model with no price written down is refused here, before a run has a
    directory or a document, and before the environment is read.
    """
    price_of(model)
    return AzureExtractor(analyses=client(), model=model)


def price_of(model: str) -> Price:
    """The rate written down for a model, or a refusal to read with it."""
    try:
        return PRICES[model]
    except KeyError:
        raise ExtractionError(
            f"no price is written down for {model}: add it to "
            "extraction/azure.py with the date it was read",
            retryable=False,
        ) from None


class Poller(Protocol):
    """An accepted analysis, as `LROPoller` exposes it."""

    def result(self, timeout: float | None = None) -> object: ...

    def done(self) -> bool: ...


class Analyses(Protocol):
    """The one SDK method this backend calls, named as the SDK names it.

    `DocumentIntelligenceClient` satisfies this, and so does a stub that
    returns a prepared poller, which is what lets a test drive the backend
    without an endpoint, a key or a network.
    """

    def begin_analyze_document(self, model_id: str, body: IO[bytes]) -> Poller: ...


@dataclass(frozen=True)
class AzureExtractor:
    """Reads a document's public PDF with one prebuilt Azure model."""

    analyses: Analyses
    model: str = MODEL

    @property
    def price(self) -> Price:
        return price_of(self.model)

    def extract(self, document: Document) -> Extraction:
        """Read one document, or say why it could not be read, and what that cost."""
        price = self.price
        try:
            body = document.path.read_bytes()
        except OSError as error:
            raise ExtractionError(
                f"cannot read the public copy: {error}", retryable=False
            ) from error
        sent = Usage(pages=document.pages)
        started = time.perf_counter()
        try:
            poller = self.analyses.begin_analyze_document(self.model, io.BytesIO(body))
        except Exception as error:
            raise ExtractionError(
                f"the analyze request was refused: {type(error).__name__}: {error}",
                retryable=retryable(error),
            ) from error
        try:
            answer = poller.result(timeout=TIMEOUT)
        except Exception as error:
            raise _abandoned(f"{type(error).__name__}: {error}", price, sent) from error
        latency = time.perf_counter() - started
        if not poller.done():
            raise _abandoned(f"still running after {TIMEOUT:.0f} s", price, sent)
        if not isinstance(answer, AnalyzeResult):
            raise _abandoned(
                f"the poller returned {type(answer).__name__}, not an analyze result",
                price,
                sent,
            )
        pages = answer.pages
        if pages is None:
            raise _abandoned("the result reports no pages", price, sent)
        usage = Usage(pages=len(pages))
        cost = price.of(usage)
        if usage.pages < document.pages:
            raise ExtractionError(
                f"Azure processed {usage.pages} of the {document.pages} pages sent; "
                "on the F0 tier only the first two are, so check the resource is S0",
                cost,
                usage=usage,
                retryable=False,
            )
        return _extraction(answer, usage, cost, latency)


def _abandoned(why: str, price: Price, sent: Usage) -> ExtractionError:
    """An accepted analysis that reported no page count, charged for pages sent."""
    return ExtractionError(
        f"the analysis was accepted and then gave no page count ({why}); charged "
        f"for the {sent.pages} pages sent",
        price.of(sent),
        usage=sent,
    )


def _extraction(
    answer: AnalyzeResult, usage: Usage, cost: Decimal, latency: float
) -> Extraction:
    header: dict[str, list[str]] = {}
    header_confidence: dict[str, list[float | None]] = {}
    rows: list[WrittenValues] = []
    row_confidence: list[dict[str, float | None]] = []
    symbols: dict[str, list[str]] = {}

    def keep(fieldtype: str, found: DocumentField) -> None:
        printed = found.content
        if printed:
            header.setdefault(fieldtype, []).append(printed)
            header_confidence.setdefault(fieldtype, []).append(found.confidence)

    for fields in _fields(answer):
        for name, fieldtype in HEADER.items():
            if name in fields:
                keep(fieldtype, fields[name])
        for name, subfields in HEADER_ROWS.items():
            for row in _rows(fields.get(name)):
                for subname, fieldtype in subfields.items():
                    if subname in row:
                        keep(fieldtype, row[subname])
        for row in _rows(fields.get("Items")):
            cells: WrittenValues = {}
            confidence: dict[str, float | None] = {}
            for subname, fieldtype in CELLS.items():
                cell = row.get(subname)
                printed = None if cell is None else cell.content
                if cell is not None and printed:
                    cells[fieldtype] = printed
                    confidence[fieldtype] = cell.confidence
            if cells:
                rows.append(cells)
                row_confidence.append(confidence)
        for name, fieldtype in CURRENCY_SOURCES.items():
            currency = fields[name].value_currency if name in fields else None
            symbol = None if currency is None else currency.currency_symbol
            if symbol:
                symbols.setdefault(fieldtype, []).append(symbol)

    written: WrittenValues = {fieldtype: values for fieldtype, values in header.items()}
    return Extraction(
        prediction=Prediction(fields=written, line_items=tuple(rows)),
        usage=usage,
        cost=cost,
        latency=latency,
        served_model=_served(answer),
        confidence=Confidence(
            fields={
                fieldtype: tuple(values)
                for fieldtype, values in header_confidence.items()
            },
            line_items=tuple(row_confidence),
        ),
        currency_symbols={
            fieldtype: tuple(found) for fieldtype, found in symbols.items()
        },
    )


def _fields(answer: AnalyzeResult) -> list[Mapping[str, DocumentField]]:
    """Every analyzed invoice's fields; a PDF can hold more than one invoice."""
    return [each.fields or {} for each in answer.documents or []]


def _rows(found: DocumentField | None) -> Sequence[Mapping[str, DocumentField]]:
    """The objects of an array field, each a row of subfields."""
    if found is None:
        return []
    return [each.value_object or {} for each in found.value_array or []]


def _served(answer: AnalyzeResult) -> str | None:
    """`modelId` with `apiVersion`, the served model Azure reports (#36)."""
    model_id = answer.model_id
    if not model_id:
        return None
    version = answer.api_version
    return f"{model_id}@{version}" if version else model_id
