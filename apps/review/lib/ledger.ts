// What the page derives from one document's view: the ledger rows, the header
// reading, and what is still open. Pure, so it reads the same on every render.

import type { Finding, Place, Values, View, Written } from "./api";

/** Each line cell's fieldtypes, first one carried wins: the matcher's own
 * table (engine/src/docmatch/matching/records.py, CELL_FIELDTYPES). */
const CELL_FIELDTYPES = {
  code: ["line_item_code"],
  description: ["line_item_description"],
  quantity: ["line_item_quantity"],
  unit: ["line_item_units_of_measure"],
  "unit price": ["line_item_unit_price_gross", "line_item_unit_price_net"],
  amount: ["line_item_amount_gross", "line_item_amount_net"],
} as const;

export type Cell = keyof typeof CELL_FIELDTYPES;

/** One value as the backend read it, with its own confidence when it gave one. */
export type Read = { text: string; confidence: number | null };

export type Row = {
  key: string;
  /** The purchase-order line, or null for a line only the invoice has. */
  po: number | null;
  /** The invoice line as read, or null for an order line nobody billed. */
  invoice: number | null;
  ordered: Partial<Record<Cell, string>>;
  received: string | null;
  billed: Partial<Record<Cell, Read>>;
  findings: Finding[];
  resolved: { sku: string | null; score: number } | null;
};

function texts(value: string | string[] | null | undefined): string | null {
  if (value == null) return null;
  const all = Array.isArray(value) ? value : [value];
  return all.length ? all.join(", ") : null;
}

function orderedCells(line: Values): Partial<Record<Cell, string>> {
  const cells: Partial<Record<Cell, string>> = {};
  for (const [cell, fieldtypes] of Object.entries(CELL_FIELDTYPES) as [Cell, readonly string[]][]) {
    const found = fieldtypes.map((each) => texts(line[each])).find((each) => each != null);
    if (found != null) cells[cell] = found;
  }
  return cells;
}

function billedCells(
  row: Written,
  confidence: Record<string, number | null> | undefined,
): Partial<Record<Cell, Read>> {
  const cells: Partial<Record<Cell, Read>> = {};
  for (const [cell, fieldtypes] of Object.entries(CELL_FIELDTYPES) as [Cell, readonly string[]][]) {
    const fieldtype = fieldtypes.find((each) => texts(row[each]) != null);
    if (fieldtype) {
      cells[cell] = { text: texts(row[fieldtype])!, confidence: confidence?.[fieldtype] ?? null };
    }
  }
  return cells;
}

/** One row per purchase-order line in the order's order, then a row per
 * invoice line the order has no line for. */
export function ledger(view: View): Row[] {
  const order = view.case.purchase_order.lines;
  const lines = view.reading?.prediction.line_items ?? [];
  const confidence = view.reading?.confidence?.line_items ?? [];
  const pairings = view.match?.pairings ?? [];
  const findings = view.match?.findings ?? [];
  const on = (kind: Place["kind"], line: number) =>
    findings.filter((each) => each.place.kind === kind && each.place.line === line);
  const invoiceCells = (line: number) => ({
    billed: billedCells(lines[line], confidence[line]),
    resolved: view.resolution?.[line] ?? null,
  });

  const rows: Row[] = order.map((line, po) => {
    const paired = pairings.find((each) => each.po_line === po);
    const received = view.case.receipt.lines.find((each) => each.po_line === po);
    return {
      key: `po-${po}`,
      po,
      invoice: paired?.invoice_line ?? null,
      ordered: orderedCells(line),
      received: texts(received?.cells.line_item_quantity),
      findings: on("po line", po),
      ...(paired ? invoiceCells(paired.invoice_line) : { billed: {}, resolved: null }),
    };
  });
  lines.forEach((_, line) => {
    if (pairings.some((each) => each.invoice_line === line)) return;
    rows.push({
      key: `invoice-${line}`,
      po: null,
      invoice: line,
      ordered: {},
      received: null,
      findings: on("invoice line", line),
      ...invoiceCells(line),
    });
  });
  return rows;
}

const HEADER_LABELS: Record<string, string> = {
  vendor_name: "Vendor",
  document_id: "Invoice number",
  order_id: "Order number",
  date_issue: "Issued",
  date_due: "Due",
  amount_total_net: "Total net",
  amount_total_tax: "Tax",
  amount_total_gross: "Total",
  amount_due: "Amount due",
  currency_code_amount_due: "Currency",
  tax_detail_rate: "Tax rate",
};

/** A header fieldtype as the page names it. */
export function label(fieldtype: string): string {
  return HEADER_LABELS[fieldtype] ?? fieldtype.replaceAll("_", " ");
}

/** The header's fields as read, known ones first in the order a reader
 * checks them, each value beside its confidence. */
export function header(view: View): { fieldtype: string; label: string; values: Read[] }[] {
  const fields = view.reading?.prediction.fields ?? {};
  const confidence = view.reading?.confidence?.fields ?? {};
  const known = Object.keys(HEADER_LABELS);
  const fieldtypes = [
    ...known.filter((each) => each in fields),
    ...Object.keys(fields).filter((each) => !known.includes(each)).sort(),
  ];
  return fieldtypes.flatMap((fieldtype) => {
    const value = fields[fieldtype];
    if (value == null) return [];
    const values = Array.isArray(value) ? value : [value];
    return [
      {
        fieldtype,
        label: label(fieldtype),
        values: values.map((text, index) => ({
          text,
          confidence: confidence[fieldtype]?.[index] ?? null,
        })),
      },
    ];
  });
}

/** A place the way the engine's explanations name it. */
export function placeName(place: Place): string {
  const kind = place.kind.replace("po", "PO");
  return place.line == null ? kind : `${kind} ${place.line}`;
}

/** Whether a routing reason still stands on the view as it is now. A pipeline
 * failure is never cleared from the page. */
export function stillOpen(view: View, reason: string): boolean {
  if (reason === "extraction failed") return view.reading == null;
  if (reason === "gate failed") return view.gate?.verdict === "failed";
  if (reason === "match held") return view.match?.verdict === "held";
  return true;
}

/** What approving would override as the document stands now, one line each:
 * a failed extraction, every failed gate rule, every hold, a pipeline failure.
 * Empty means approving is one click. */
export function openItems(view: View): string[] {
  const failures = view.routing_reasons.filter((each) => each.startsWith("pipeline failed"));
  return [
    ...(view.reading == null && !failures.length ? ["extraction failed"] : []),
    ...(view.gate?.checks ?? [])
      .filter((check) => check.outcome === "failed")
      .map((check) => `gate failed: ${check.rule}`),
    ...(view.match?.findings ?? [])
      .filter((finding) => finding.severity === "hold")
      .map((finding) => `${finding.type} on ${placeName(finding.place)}`),
    ...failures,
  ];
}
