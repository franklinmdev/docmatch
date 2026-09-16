"""Tests for the Azure prebuilt invoice backend, driven through its one SDK call."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

import pytest
from azure.ai.documentintelligence.models import AnalyzeResult
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from docmatch.extraction import azure
from docmatch.extraction.azure import AzureExtractor
from docmatch.extraction.conftest import write_pdf
from docmatch.extraction.extractor import Confidence, Document, ExtractionError, Usage
from docmatch.metrics.fields import Prediction


def a_document(tmp_path: Path, *, pages: int = 1) -> Document:
    copy = write_pdf(tmp_path / "syn0001.pdf", pages=pages)
    return Document("syn0001", copy, pages)


@pytest.fixture
def document(tmp_path: Path) -> Document:
    return a_document(tmp_path)


def text(content: str | None, confidence: float | None = 0.9) -> dict[str, Any]:
    """A string field as the API returns one."""
    field: dict[str, Any] = {"type": "string", "valueString": content}
    if content is not None:
        field["content"] = content
    if confidence is not None:
        field["confidence"] = confidence
    return field


def money(
    content: str,
    amount: float,
    *,
    symbol: str | None = None,
    code: str | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    """A currency field, its typed value beside the printed text."""
    value: dict[str, Any] = {"amount": amount}
    if symbol is not None:
        value["currencySymbol"] = symbol
    if code is not None:
        value["currencyCode"] = code
    return {
        "type": "currency",
        "content": content,
        "valueCurrency": value,
        "confidence": confidence,
    }


def items(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "valueArray": [
            {"type": "object", "valueObject": row, "confidence": 0.5} for row in rows
        ],
    }


def result(
    fields: dict[str, Any],
    *,
    pages: int | None = 1,
    model_id: str = "prebuilt-invoice",
    api_version: str = "2024-11-30",
) -> AnalyzeResult:
    """An analyze result with one invoice, as the SDK deserializes one."""
    body: dict[str, Any] = {
        "apiVersion": api_version,
        "modelId": model_id,
        "stringIndexType": "textElements",
        "content": "",
        "documents": [{"docType": "invoice", "fields": fields, "confidence": 1.0}],
    }
    if pages is not None:
        body["pages"] = [
            {"pageNumber": number, "spans": []} for number in range(1, pages + 1)
        ]
    return AnalyzeResult(body)


@dataclass
class FakePoller:
    """An accepted analysis: its result, or what polling it raised."""

    outcome: object
    finished: bool = True
    waited: list[float | None] = field(default_factory=list)

    def result(self, timeout: float | None = None) -> object:
        self.waited.append(timeout)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome if self.finished else None

    def done(self) -> bool:
        return self.finished


@dataclass
class FakeAnalyses:
    """Accepts or refuses each request in turn, and remembers what it was sent."""

    answers: list[object]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def begin_analyze_document(self, model_id: str, body: IO[bytes]) -> FakePoller:
        self.calls.append({"model_id": model_id, "body": body.read()})
        answer = self.answers[len(self.calls) - 1]
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, FakePoller)
        return answer


def reading(*outcomes: object) -> tuple[AzureExtractor, FakeAnalyses]:
    analyses = FakeAnalyses(
        answers=[
            each
            if isinstance(each, (FakePoller, HttpResponseError))
            else FakePoller(each)
            for each in outcomes
        ]
    )
    return AzureExtractor(analyses=analyses), analyses


def refused(status: int) -> HttpResponseError:
    error = HttpResponseError(message=f"status {status}")
    error.status_code = status
    return error


def test_sends_the_public_copy_unchanged(document: Document) -> None:
    backend, analyses = reading(result({}))

    backend.extract(document)

    assert analyses.calls == [
        {"model_id": "prebuilt-invoice", "body": document.path.read_bytes()}
    ]


def test_maps_header_fields_onto_docile_fieldtypes_by_their_printed_text(
    document: Document,
) -> None:
    """`content` is what was printed; `valueDate` would be Azure resolving it."""
    backend, _ = reading(
        result(
            {
                "InvoiceId": text("INV-100"),
                "InvoiceDate": {
                    "type": "date",
                    "valueDate": "2019-11-15",
                    "content": "11/15/19",
                    "confidence": 0.8,
                },
                "VendorName": text("Northwind Trading"),
                "RemittanceAddressRecipient": text("Northwind Billing"),
                "CustomerName": text("Contoso"),
                "PurchaseOrder": text("PO-3333"),
                "InvoiceTotal": money("$110.00", 110.0),
            }
        )
    )

    read = backend.extract(document)

    assert read.prediction == Prediction(
        fields={
            "document_id": ["INV-100"],
            "order_id": ["PO-3333"],
            "date_issue": ["11/15/19"],
            "vendor_name": ["Northwind Trading", "Northwind Billing"],
            "customer_billing_name": ["Contoso"],
            "amount_total_gross": ["$110.00"],
        }
    )


def test_drops_an_azure_field_with_no_docile_counterpart(document: Document) -> None:
    backend, _ = reading(
        result(
            {
                "TotalDiscount": money("$5.00", 5.0),
                "ServiceStartDate": {"type": "date", "content": "10/14/2019"},
            }
        )
    )

    assert backend.extract(document).prediction == Prediction()


def test_a_field_with_no_printed_text_is_left_out(document: Document) -> None:
    """`content` is optional in the 2024-11-30 schema; its typed value is not used."""
    backend, _ = reading(result({"VendorName": text(None)}))

    assert backend.extract(document).prediction == Prediction()


def test_maps_every_item_onto_a_line_item_row(document: Document) -> None:
    backend, _ = reading(
        result(
            {
                "Items": items(
                    {
                        "Description": text("Junction box"),
                        "Quantity": {
                            "type": "number",
                            "valueNumber": 2,
                            "content": "2",
                            "confidence": 0.7,
                        },
                        "UnitPrice": money("30.00", 30.0),
                        "Amount": money("60.00", 60.0),
                        "ProductCode": text("A123"),
                    },
                    {"Description": text("Freight")},
                )
            }
        )
    )

    assert backend.extract(document).prediction.line_items == (
        {
            "line_item_description": "Junction box",
            "line_item_quantity": "2",
            "line_item_unit_price_gross": "30.00",
            "line_item_amount_gross": "60.00",
            "line_item_code": "A123",
        },
        {"line_item_description": "Freight"},
    )


def test_maps_tax_and_payment_details_onto_the_header(document: Document) -> None:
    backend, _ = reading(
        result(
            {
                "TaxDetails": items(
                    {"Amount": money("29,520.00", 29520.0), "Rate": text("18 %")}
                ),
                "PaymentDetails": items(
                    {"IBAN": text("DE94 7007"), "SWIFT": text("DEUTDEMMXXX")}
                ),
            }
        )
    )

    assert backend.extract(document).prediction.fields == {
        "tax_detail_tax": ["29,520.00"],
        "tax_detail_rate": ["18 %"],
        "iban": ["DE94 7007"],
        "bic": ["DEUTDEMMXXX"],
    }


def test_keeps_the_confidence_of_every_value_and_every_cell(
    document: Document,
) -> None:
    backend, _ = reading(
        result(
            {
                "VendorName": text("Northwind Trading", 0.93),
                "VendorAddressRecipient": text("Northwind", None),
                "Items": items(
                    {
                        "Description": text("Junction box", 0.71),
                        "Unit": text("ea", None),
                    }
                ),
            }
        )
    )

    assert backend.extract(document).confidence == Confidence(
        fields={"vendor_name": (0.93, None)},
        line_items=(
            {"line_item_description": 0.71, "line_item_units_of_measure": None},
        ),
    )


def test_keeps_the_currency_symbol_and_never_the_code(document: Document) -> None:
    backend, _ = reading(
        result(
            {
                "AmountDue": money("95.00", 95.0, symbol="$", code="USD"),
                "InvoiceTotal": money("95.00", 95.0, symbol="$"),
                "SubTotal": money("80.00", 80.0, symbol="€"),
            }
        )
    )

    read = backend.extract(document)

    assert read.currency_symbols == {
        "amount_due": ("$",),
        "amount_total_gross": ("$",),
    }
    assert "USD" not in str(read.prediction)


def test_charges_the_pages_azure_processed(tmp_path: Path) -> None:
    backend, _ = reading(result({}, pages=3))

    read = backend.extract(a_document(tmp_path, pages=3))

    assert read.usage == Usage(pages=3)
    assert read.cost == Decimal("0.03")


def test_the_served_model_is_the_model_id_with_the_api_version(
    document: Document,
) -> None:
    backend, _ = reading(
        result({}, model_id="prebuilt-invoice", api_version="2024-11-30")
    )

    assert backend.extract(document).served_model == "prebuilt-invoice@2024-11-30"


def test_a_page_gap_fails_at_once_charged_for_the_pages_processed(
    tmp_path: Path,
) -> None:
    backend, _ = reading(result({}, pages=2))

    with pytest.raises(ExtractionError) as raised:
        backend.extract(a_document(tmp_path, pages=3))

    assert "2 of the 3 pages" in str(raised.value)
    assert not raised.value.retryable
    assert raised.value.cost == Decimal("0.02")
    assert raised.value.usage == Usage(pages=2)


def test_an_accepted_analysis_that_reports_no_pages_is_charged_for_the_pages_sent(
    tmp_path: Path,
) -> None:
    backend, _ = reading(result({}, pages=None))

    with pytest.raises(ExtractionError) as raised:
        backend.extract(a_document(tmp_path, pages=3))

    assert raised.value.retryable
    assert raised.value.cost == Decimal("0.03")
    assert raised.value.usage == Usage(pages=3)


@pytest.mark.parametrize(
    "poller",
    [
        FakePoller(ServiceRequestError("the connection went away")),
        FakePoller(refused(500)),
        FakePoller(result({}), finished=False),
    ],
    ids=["dropped", "operation failed", "still running"],
)
def test_polling_that_ends_without_a_result_is_charged_for_the_pages_sent(
    tmp_path: Path, poller: FakePoller
) -> None:
    """Accepted and abandoned: Azure does not document how that is billed (#35)."""
    backend, _ = reading(poller)

    with pytest.raises(ExtractionError) as raised:
        backend.extract(a_document(tmp_path, pages=2))

    assert raised.value.retryable
    assert raised.value.cost == Decimal("0.02")
    assert raised.value.usage == Usage(pages=2)


def test_waits_for_the_analysis_no_longer_than_its_timeout(document: Document) -> None:
    poller = FakePoller(result({}))
    backend, _ = reading(poller)

    backend.extract(document)

    assert poller.waited == [azure.TIMEOUT]


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (refused(429), True),
        (refused(503), True),
        (refused(401), False),
        (refused(400), False),
        (ServiceRequestError("no route to host"), True),
    ],
)
def test_a_refused_request_costs_nothing_and_is_retried_by_its_status(
    document: Document, error: Exception, retryable: bool
) -> None:
    analyses = FakeAnalyses(answers=[error])
    backend = AzureExtractor(analyses=analyses)

    with pytest.raises(ExtractionError) as raised:
        backend.extract(document)

    assert raised.value.retryable is retryable
    assert raised.value.cost == 0
    assert raised.value.usage == Usage()


def test_refuses_an_unpriced_model_before_any_request(document: Document) -> None:
    backend, analyses = reading(result({}))

    with pytest.raises(ExtractionError, match="no price is written down"):
        AzureExtractor(analyses=analyses, model="prebuilt-receipt").extract(document)

    assert analyses.calls == []
    assert backend.model == azure.MODEL


def test_a_missing_key_is_named_in_the_engines_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        azure.ENDPOINT_VARIABLE, "https://example.cognitiveservices.azure.com/"
    )
    monkeypatch.delenv(azure.KEY_VARIABLE, raising=False)

    with pytest.raises(ExtractionError, match=f"no \\${azure.KEY_VARIABLE}"):
        azure.extractor()


def test_a_missing_endpoint_is_named_in_the_engines_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(azure.ENDPOINT_VARIABLE, raising=False)
    monkeypatch.setenv(azure.KEY_VARIABLE, "key")

    with pytest.raises(ExtractionError, match=f"no \\${azure.ENDPOINT_VARIABLE}"):
        azure.extractor()


def test_the_client_reads_with_the_pinned_api_version_and_no_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        azure.ENDPOINT_VARIABLE, "https://example.cognitiveservices.azure.com/"
    )
    monkeypatch.setenv(azure.KEY_VARIABLE, "key")

    client = azure.client()

    assert client._config.api_version == "2024-11-30"
    assert client._config.retry_policy.total_retries == 0
