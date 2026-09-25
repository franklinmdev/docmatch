// The engine's HTTP API as the page reads it (engine/src/docmatch/pipeline/api.py).
// Only the fields the page draws are typed; the rest of the view is ignored.

export const API_URL = process.env.DOCMATCH_API_URL ?? "http://127.0.0.1:8000";

export type Status =
  | "received"
  | "extracted"
  | "validated"
  | "resolved"
  | "matched"
  | "needs_review"
  | "approved"
  | "rejected";

export type Decision = "approved" | "rejected";

/** A fieldtype to one text or several, as a reading writes it. */
export type Written = Record<string, string | string[] | null>;

/** A fieldtype to its texts, as a case writes it. */
export type Values = Record<string, string[]>;

export type Place = { kind: "po line" | "invoice line" | "header"; line: number | null };

export type Finding = {
  type: string;
  place: Place;
  cell?: string;
  severity: "hold" | "note";
  explanation: string;
};

export type NotCompared = {
  place: Place;
  cell: string | null;
  texts: string[];
  reason: string;
};

export type GateCheck = {
  rule: string;
  outcome: "passed" | "failed" | "absent" | "unreadable";
  used: [string, string][];
};

/** One edit to the reading; the order and the receipt take none (#154). */
export type Change =
  | { kind: "header"; fieldtype: string; value: string }
  | { kind: "cell"; line: number; fieldtype: string; value: string }
  | { kind: "line removed" | "line restored"; line: number }
  | { kind: "line added" };

/** A net change to the reading: the value read against the last value left. */
export type Correction =
  | { kind: "header"; fieldtype: string; read: string[]; left: string[] }
  | { kind: "cell"; line: number; fieldtype: string; read: string[]; left: string[] }
  | { kind: "line removed"; line: number; read: Values }
  | { kind: "line added"; line: number; left: Values };

export type View = {
  id: number;
  status: Status;
  pages: number;
  backend: string;
  case: {
    purchase_order: { header: Values; lines: Values[] };
    receipt: { lines: { cells: Values; po_line: number }[] };
  };
  reading: {
    prediction: { fields?: Written; line_items?: Written[] };
    confidence: {
      fields: Record<string, (number | null)[]>;
      line_items: Record<string, number | null>[];
    } | null;
  } | null;
  gate: { verdict: "passed" | "failed" | "not checked"; checks: GateCheck[] } | null;
  resolution: ({ sku: string | null; score: number } | null)[] | null;
  match: {
    verdict: "held" | "approvable";
    pairings: { invoice_line: number; po_line: number }[];
    findings: Finding[];
    not_compared: NotCompared[];
  } | null;
  /** The line, by its name among the edits, each place of the reading holds. */
  line_ids: number[];
  corrections: Correction[];
  routing_reasons: string[];
  cost: string;
  latency: number | null;
};

export type Queued = { id: number; status: Status; routing_reasons: string[] };

export class ApiError extends Error {}

async function call(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`The API at ${API_URL} did not answer.`);
  }
}

export async function queue(): Promise<Queued[]> {
  const response = await call("/documents?status=needs_review");
  if (!response.ok) throw new ApiError(`The queue answered ${response.status}.`);
  return ((await response.json()) as { documents: Queued[] }).documents;
}

export async function view(id: number): Promise<View | null> {
  const response = await call(`/documents/${id}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new ApiError(`Document ${id} answered ${response.status}.`);
  return (await response.json()) as View;
}

/** The decision, or the API's refusal as a sentence. */
export async function decide(id: number, decision: Decision): Promise<string | null> {
  const response = await call(`/documents/${id}/decision`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (response.ok) return null;
  if (response.status === 409) return `Document ${id} is no longer in review.`;
  return `The decision answered ${response.status}.`;
}

/** One edit, saved and rerun by the engine; its refusal as a sentence. */
export async function edit(id: number, change: Change): Promise<string | null> {
  const response = await call(`/documents/${id}/edits`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(change),
  });
  if (response.ok) return null;
  if (response.status === 409) return `Document ${id} can no longer be edited.`;
  if (response.status === 422) {
    const { detail } = (await response.json()) as { detail: unknown };
    return typeof detail === "string" ? `The reading cannot take that: ${detail}.` : "The reading cannot take that edit.";
  }
  return `The edit answered ${response.status}.`;
}
