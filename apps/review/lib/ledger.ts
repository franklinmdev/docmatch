// What the page derives from one document's view: the ledger rows, the header
// reading, the corrections, and what is still open. Pure, so it reads the same
// on every render.

import type { Correction, Finding, Place, Values, View, Written } from "./api";

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

const CELLS = Object.keys(CELL_FIELDTYPES) as Cell[];

/** One value as the backend read it, with its own confidence when it gave one. */
export type Read = { text: string; confidence: number | null };

export type Row = {
  key: string;
  /** The purchase-order line, or null for a line only the invoice has. */
  po: number | null;
  /** The invoice line's place in the reading, or null for an order line
   * nobody billed. */
  invoice: number | null;
  /** The invoice line's name, which an edit names it by. */
  line: number | null;
  /** The fieldtype an edit to each cell names: the one the line carries,
   * else the matcher's first. */
  fieldtypes: Record<Cell, string>;
  /** Each corrected cell's value as read, "" when it was not read. */
  was: Partial<Record<Cell, string>>;
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

function editedFieldtypes(row: Written): Record<Cell, string> {
  return Object.fromEntries(
    CELLS.map((cell) => {
      const fieldtypes: readonly string[] = CELL_FIELDTYPES[cell];
      return [cell, fieldtypes.find((each) => texts(row[each]) != null) ?? fieldtypes[0]];
    }),
  ) as Record<Cell, string>;
}

function wasRead(corrections: Correction[], line: number, row: Written): Partial<Record<Cell, string>> {
  const fieldtypes = editedFieldtypes(row);
  const was: Partial<Record<Cell, string>> = {};
  for (const each of corrections) {
    if (each.kind !== "cell" || each.line !== line) continue;
    const cell = CELLS.find((one) => fieldtypes[one] === each.fieldtype);
    if (cell) was[cell] = each.read.join(", ");
  }
  return was;
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
    line: view.line_ids[line] ?? line,
    billed: billedCells(lines[line], confidence[line]),
    fieldtypes: editedFieldtypes(lines[line]),
    was: wasRead(view.corrections, view.line_ids[line] ?? line, lines[line]),
    resolved: view.resolution?.[line] ?? null,
  });
  const unbilled = { line: null, billed: {}, fieldtypes: editedFieldtypes({}), was: {}, resolved: null };

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
      ...(paired ? invoiceCells(paired.invoice_line) : unbilled),
    };
  });
  lines.forEach((_, line) => {
    if (pairings.some((each) => each.invoice_line === line)) return;
    rows.push({
      key: `invoice-${view.line_ids[line] ?? line}`,
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

export type HeaderField = {
  fieldtype: string;
  label: string;
  values: Read[];
  /** The value as read, "" when it was not, once a correction changed it. */
  was: string | null;
};

/** The header's fields as they stand, known ones first in the order a
 * reader checks them, each value beside its confidence. With `unread`, every
 * known field is listed, so one the backend missed can be filled in. */
export function header(view: View, unread = false): HeaderField[] {
  const fields = view.reading?.prediction.fields ?? {};
  const confidence = view.reading?.confidence?.fields ?? {};
  const corrected = new Map(
    view.corrections.flatMap((each) => (each.kind === "header" ? [[each.fieldtype, each.read.join(", ")]] : [])),
  );
  const known = Object.keys(HEADER_LABELS);
  const listed = (fieldtype: string) => fields[fieldtype] != null || corrected.has(fieldtype);
  const fieldtypes = [
    ...known.filter((each) => unread || listed(each)),
    ...[...new Set([...Object.keys(fields), ...corrected.keys()])]
      .filter((each) => !known.includes(each) && listed(each))
      .sort(),
  ];
  return fieldtypes.map((fieldtype) => {
    const value = fields[fieldtype];
    const values = value == null ? [] : Array.isArray(value) ? value : [value];
    return {
      fieldtype,
      label: label(fieldtype),
      values: values.map((text, index) => ({
        text,
        confidence: confidence[fieldtype]?.[index] ?? null,
      })),
      was: corrected.get(fieldtype) ?? null,
    };
  });
}

/** The lines read and then removed, by name, each with its description. */
export function removedLines(view: View): { line: number; description: string | null }[] {
  return view.corrections.flatMap((each) =>
    each.kind === "line removed"
      ? [{ line: each.line, description: texts(each.read.line_item_description) }]
      : [],
  );
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
