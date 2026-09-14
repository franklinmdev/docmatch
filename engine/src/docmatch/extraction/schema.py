"""The shape a vision backend is asked to fill in, and what it becomes.

A backend is given this schema and returns an `Invoice`. Nothing else in the
engine sees that type: `Invoice.prediction()` turns it into the `Prediction`
the scorers already take, so a backend's output and a hand-written prediction
file are the same thing by the time anything is measured.

The fields are DocILE's own vocabulary, all 36 KILE fieldtypes and all 19 LIR
fieldtypes, named exactly as the labels name them. That is deliberate: a
schema with prettier names would need a mapping table between the names the
model was asked for and the names the labels use, and every disagreement about
that table would show up in the score as an extraction error. Naming the
fieldtypes after the labels makes the mapping the identity function.

One value or several
--------------------

Every header fieldtype takes a list, and every line-item cell takes a single
value. Both halves are measured over the annotated set.

For the header, a single value per fieldtype would cap recall at 94.4% before
the model reads anything: of the 5,234 distinct normalized header values in the
val split, 292 are the second or later value of their fieldtype in the same
document. The repetition is not spread evenly, and it is not the tax breakdown
one would guess: 25.2% of documents print more than one distinct
`vendor_address`, 17.1% more than one `order_id` and 14.5% more than one
`vendor_name`, while `amount_total_gross` does so in 0.6% and
`currency_code_amount_due` never. A list costs nothing where the document has
one value, because `metrics.fields` compares distinct normalized values and a
value reported twice collapses into one, so the only thing a list can cost is a
value the page does not carry. That is a precision risk this benchmark measures
rather than assumes.

A line item is the other way round. Within one row a repeated fieldtype is rare
enough that `metrics.line_items` scores a row by its distinct cells, so a list
there would add a way to be wrong without adding a way to be right.

Absence is explicit
-------------------

An empty list, and a `null` cell, mean the document does not carry the field.
They are not "the model did not look". `metrics.fields` writes both down as an
absence that is neither predicted nor wrong, so a backend that says nothing
about a field it cannot see is treated exactly like a human-written prediction
that says `null`. Rule 4 of `CLAUDE.md` is the reason this is a schema and not
a confidence: what a model returns is structure, and no part of it gates
acceptance on its own.
"""

from pydantic import BaseModel, ConfigDict, Field

from docmatch.metrics.fields import Prediction, WrittenValues


def _one(description: str) -> str:
    """A cell's description, with the rule every cell shares appended."""
    return f"{description} Null if the row does not show it."


def _many(description: str) -> str:
    """A header field's description, with the rule every field shares appended."""
    return (
        f"{description} Every distinct value printed, empty if the document shows none."
    )


class LineItem(BaseModel):
    """One row of a document's line-item table, one value per cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_item_position: str | None = Field(
        default=None, description=_one("The row's number as printed.")
    )
    line_item_code: str | None = Field(
        default=None,
        description=_one("Article, SKU or product code, exactly as printed."),
    )
    line_item_description: str | None = Field(
        default=None, description=_one("What the row is for.")
    )
    line_item_quantity: str | None = Field(default=None, description=_one("How many."))
    line_item_units_of_measure: str | None = Field(
        default=None,
        description=_one("The unit the quantity counts, such as pcs or kg."),
    )
    line_item_unit_price_net: str | None = Field(
        default=None, description=_one("Price for one unit, before tax.")
    )
    line_item_unit_price_gross: str | None = Field(
        default=None, description=_one("Price for one unit, including tax.")
    )
    line_item_amount_net: str | None = Field(
        default=None, description=_one("Row total before tax.")
    )
    line_item_amount_gross: str | None = Field(
        default=None, description=_one("Row total including tax.")
    )
    line_item_tax: str | None = Field(
        default=None, description=_one("Tax amount on the row.")
    )
    line_item_tax_rate: str | None = Field(
        default=None, description=_one("Tax rate on the row, such as 21%.")
    )
    line_item_discount_amount: str | None = Field(
        default=None, description=_one("Discount subtracted on the row.")
    )
    line_item_discount_rate: str | None = Field(
        default=None, description=_one("Discount rate on the row.")
    )
    line_item_currency: str | None = Field(
        default=None, description=_one("Currency of the row's amounts.")
    )
    line_item_date: str | None = Field(
        default=None,
        description=_one("A date printed on the row, such as a service date."),
    )
    line_item_order_id: str | None = Field(
        default=None, description=_one("An order number printed on the row.")
    )
    line_item_person_name: str | None = Field(
        default=None, description=_one("A person named on the row.")
    )
    line_item_weight: str | None = Field(
        default=None, description=_one("Weight on the row.")
    )
    line_item_hts_number: str | None = Field(
        default=None, description=_one("Customs tariff or HTS number on the row.")
    )


class Invoice(BaseModel):
    """What a backend claims one document says, in DocILE's own vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: list[str] = Field(
        default_factory=list, description=_many("The invoice or document number.")
    )
    order_id: list[str] = Field(
        default_factory=list,
        description=_many("A purchase order number the document cites."),
    )
    customer_order_id: list[str] = Field(
        default_factory=list,
        description=_many("An order number belonging to the customer."),
    )
    vendor_order_id: list[str] = Field(
        default_factory=list,
        description=_many("An order number belonging to the vendor."),
    )
    payment_reference: list[str] = Field(
        default_factory=list, description=_many("A reference to quote when paying.")
    )
    date_issue: list[str] = Field(
        default_factory=list, description=_many("The date the document was issued.")
    )
    date_due: list[str] = Field(
        default_factory=list, description=_many("The date payment is due.")
    )
    payment_terms: list[str] = Field(
        default_factory=list,
        description=_many("Payment terms as printed, such as Net 30."),
    )
    vendor_name: list[str] = Field(
        default_factory=list, description=_many("The party issuing the document.")
    )
    vendor_address: list[str] = Field(
        default_factory=list, description=_many("The issuing party's address.")
    )
    vendor_email: list[str] = Field(
        default_factory=list, description=_many("The issuing party's email address.")
    )
    vendor_tax_id: list[str] = Field(
        default_factory=list,
        description=_many("The issuing party's tax or VAT number."),
    )
    vendor_registration_id: list[str] = Field(
        default_factory=list,
        description=_many("The issuing party's company registration number."),
    )
    customer_id: list[str] = Field(
        default_factory=list,
        description=_many("The customer's account number with the vendor."),
    )
    customer_billing_name: list[str] = Field(
        default_factory=list, description=_many("The party being billed.")
    )
    customer_billing_address: list[str] = Field(
        default_factory=list, description=_many("The billed party's address.")
    )
    customer_delivery_name: list[str] = Field(
        default_factory=list,
        description=_many("The party the goods go to, when named separately."),
    )
    customer_delivery_address: list[str] = Field(
        default_factory=list,
        description=_many("Where the goods go, when given separately."),
    )
    customer_other_name: list[str] = Field(
        default_factory=list, description=_many("Another party named on the document.")
    )
    customer_other_address: list[str] = Field(
        default_factory=list, description=_many("Another address on the document.")
    )
    customer_tax_id: list[str] = Field(
        default_factory=list, description=_many("The customer's tax or VAT number.")
    )
    customer_registration_id: list[str] = Field(
        default_factory=list,
        description=_many("The customer's company registration number."),
    )
    amount_total_net: list[str] = Field(
        default_factory=list, description=_many("Total before tax.")
    )
    amount_total_tax: list[str] = Field(
        default_factory=list, description=_many("Total tax charged.")
    )
    amount_total_gross: list[str] = Field(
        default_factory=list, description=_many("Total including tax.")
    )
    amount_due: list[str] = Field(
        default_factory=list, description=_many("What is left to pay.")
    )
    amount_paid: list[str] = Field(
        default_factory=list, description=_many("What has already been paid.")
    )
    currency_code_amount_due: list[str] = Field(
        default_factory=list, description=_many("The currency of the amount due.")
    )
    tax_detail_net: list[str] = Field(
        default_factory=list,
        description=_many("Net amount of one line of the tax breakdown table."),
    )
    tax_detail_tax: list[str] = Field(
        default_factory=list,
        description=_many("Tax amount of one line of the tax breakdown table."),
    )
    tax_detail_gross: list[str] = Field(
        default_factory=list,
        description=_many("Gross amount of one line of the tax breakdown table."),
    )
    tax_detail_rate: list[str] = Field(
        default_factory=list,
        description=_many("Rate of one line of the tax breakdown table."),
    )
    iban: list[str] = Field(
        default_factory=list, description=_many("An IBAN to pay into.")
    )
    bic: list[str] = Field(
        default_factory=list, description=_many("A BIC or SWIFT code.")
    )
    account_num: list[str] = Field(
        default_factory=list, description=_many("A bank account number.")
    )
    bank_num: list[str] = Field(
        default_factory=list, description=_many("A bank, sort or routing code.")
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description="Every row of the line-item table, in the order printed.",
    )

    def prediction(self) -> Prediction:
        """This reading as the thing the scorers take.

        Empty lists and null cells are dropped rather than written down as
        empty values, because `Prediction` already treats a fieldtype it does
        not carry as an absence, and carrying it explicitly would say the same
        thing in more bytes.
        """
        header: WrittenValues = {
            fieldtype: values
            for fieldtype, values in self.model_dump(exclude={"line_items"}).items()
            if values
        }
        rows = tuple(
            {
                fieldtype: value
                for fieldtype, value in row.model_dump().items()
                if value is not None
            }
            for row in self.line_items
        )
        return Prediction(fields=header, line_items=rows)


HEADER_FIELDTYPES = tuple(name for name in Invoice.model_fields if name != "line_items")
"""The KILE fieldtypes the schema asks for, in the order it asks for them."""

CELL_FIELDTYPES = tuple(LineItem.model_fields)
"""The LIR fieldtypes one row of the schema asks for."""
