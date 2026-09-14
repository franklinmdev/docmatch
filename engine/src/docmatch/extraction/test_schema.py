"""Tests for the shape a backend fills in, and what it becomes."""

from docmatch.extraction.schema import (
    CELL_FIELDTYPES,
    HEADER_FIELDTYPES,
    Invoice,
    LineItem,
)

KILE = {
    "account_num",
    "amount_due",
    "amount_paid",
    "amount_total_gross",
    "amount_total_net",
    "amount_total_tax",
    "bank_num",
    "bic",
    "currency_code_amount_due",
    "customer_billing_address",
    "customer_billing_name",
    "customer_delivery_address",
    "customer_delivery_name",
    "customer_id",
    "customer_order_id",
    "customer_other_address",
    "customer_other_name",
    "customer_registration_id",
    "customer_tax_id",
    "date_due",
    "date_issue",
    "document_id",
    "iban",
    "order_id",
    "payment_reference",
    "payment_terms",
    "tax_detail_gross",
    "tax_detail_net",
    "tax_detail_rate",
    "tax_detail_tax",
    "vendor_address",
    "vendor_email",
    "vendor_name",
    "vendor_order_id",
    "vendor_registration_id",
    "vendor_tax_id",
}
"""The 36 KILE fieldtypes the annotated set labels, counted over `trainval`."""

LIR = {
    "line_item_amount_gross",
    "line_item_amount_net",
    "line_item_code",
    "line_item_currency",
    "line_item_date",
    "line_item_description",
    "line_item_discount_amount",
    "line_item_discount_rate",
    "line_item_hts_number",
    "line_item_order_id",
    "line_item_person_name",
    "line_item_position",
    "line_item_quantity",
    "line_item_tax",
    "line_item_tax_rate",
    "line_item_unit_price_gross",
    "line_item_unit_price_net",
    "line_item_units_of_measure",
    "line_item_weight",
}
"""The 19 LIR fieldtypes, likewise."""


def test_asks_for_every_fieldtype_the_labels_use() -> None:
    """A fieldtype the schema omits cannot be predicted, so it scores as a miss.

    The two sets are the vocabulary the scorers compare against. Naming them
    here means a schema that drifts from the labels fails a test instead of
    quietly losing recall on whatever it dropped.
    """
    assert set(HEADER_FIELDTYPES) == KILE
    assert set(CELL_FIELDTYPES) == LIR


def test_a_header_field_carries_every_value_and_a_cell_carries_one() -> None:
    assert Invoice.model_fields["vendor_name"].annotation == list[str]
    assert LineItem.model_fields["line_item_quantity"].annotation == (str | None)


def test_turns_a_reading_into_a_prediction() -> None:
    reading = Invoice(
        vendor_name=["Northwind Trading Ltd"],
        tax_detail_rate=["18%", "0%"],
        line_items=[
            LineItem(line_item_description="Blue widget", line_item_quantity="2"),
            LineItem(line_item_description="Red widget", line_item_quantity="1"),
        ],
    )

    prediction = reading.prediction()

    assert prediction.fields["vendor_name"] == ["Northwind Trading Ltd"]
    assert prediction.fields["tax_detail_rate"] == ["18%", "0%"]
    assert prediction.header["vendor_name"] == ("Northwind Trading Ltd",)
    assert [row["line_item_description"] for row in prediction.line_items] == [
        "Blue widget",
        "Red widget",
    ]


def test_leaves_out_what_the_document_does_not_carry() -> None:
    """An empty list and a null cell are an absence, and absence is silence.

    `metrics.fields` already treats a fieldtype a prediction does not mention
    as absent, so writing all 36 of them down as empty would say the same thing
    in more bytes and give a reader one more thing to read past.
    """
    reading = Invoice(
        vendor_name=["Northwind Trading Ltd"],
        line_items=[LineItem(line_item_description="Blue widget")],
    )

    prediction = reading.prediction()

    assert set(prediction.fields) == {"vendor_name"}
    assert set(prediction.line_items[0]) == {"line_item_description"}


def test_a_reading_of_nothing_is_a_prediction_of_nothing() -> None:
    prediction = Invoice().prediction()

    assert prediction.fields == {}
    assert prediction.line_items == ()
