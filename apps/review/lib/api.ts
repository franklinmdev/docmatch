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
