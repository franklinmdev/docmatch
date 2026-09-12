"""The DocILE annotation shape, as Pydantic models.

DocILE labels two tasks. KILE (key information localization and extraction)
labels header-level values such as `document_id` or `amount_total_gross`. LIR
(line item recognition) labels table cells and groups them into line items.
"""

from collections import defaultdict
from functools import cached_property

from pydantic import BaseModel, ConfigDict, Field

BoundingBox = tuple[float, float, float, float]
"""Page-relative box, (left, top, right, bottom), each in [0, 1]."""


class FieldExtraction(BaseModel):
    """One KILE label: a typed value localized on a page."""

    model_config = ConfigDict(frozen=True)

    fieldtype: str
    text: str
    page: int
    bbox: BoundingBox


class LineItemCell(FieldExtraction):
    """One LIR label: a cell that belongs to a numbered line item."""

    line_item_id: int


class LineItem(BaseModel):
    """The cells of one row of a document's line-item table."""

    model_config = ConfigDict(frozen=True)

    line_item_id: int
    cells: tuple[LineItemCell, ...]


class DocumentMetadata(BaseModel):
    """What DocILE records about the document itself.

    Layout keys such as `page_to_table_grid` are deliberately not modelled;
    unknown keys are ignored rather than rejected.
    """

    model_config = ConfigDict(frozen=True)

    page_count: int
    currency: str | None = None
    document_type: str | None = None
    language: str | None = None


class Annotation(BaseModel):
    """The labels for one DocILE document."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    fields: list[FieldExtraction] = Field(alias="field_extractions")
    cells: list[LineItemCell] = Field(alias="line_item_extractions")
    metadata: DocumentMetadata

    @cached_property
    def line_items(self) -> tuple[LineItem, ...]:
        """The LIR cells grouped by line item, lowest id first.

        Cells keep their order within a line item; the file does not promise
        that line items themselves appear in order.
        """
        grouped: dict[int, list[LineItemCell]] = defaultdict(list)
        for cell in self.cells:
            grouped[cell.line_item_id].append(cell)
        return tuple(
            LineItem(line_item_id=line_item_id, cells=tuple(grouped[line_item_id]))
            for line_item_id in sorted(grouped)
        )
