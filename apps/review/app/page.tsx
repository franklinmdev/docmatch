import Link from "next/link";

import { ApiError, queue, view, type Queued, type View } from "@/lib/api";
import { header, label, ledger, openItems, placeName, stillOpen, type Read, type Row } from "@/lib/ledger";

import { DecideBar } from "./decide-bar";

const chip = "inline-flex items-center rounded-xs px-1.5 py-0.5 font-mono text-label whitespace-nowrap";
const quiet = `${chip} bg-surface text-muted`;

/** The one review page: the queue strip, and the open document's ledger
 * beside its scanned page (#156). The open document's id is in the URL. */
export default async function Page({ searchParams }: PageProps<"/">) {
  const { doc } = await searchParams;
  let queued: Queued[];
  let shown: View | null = null;
  const asked = Number(doc);
  try {
    queued = await queue();
    const id = Number.isInteger(asked) && asked > 0 ? asked : queued[0]?.id;
    if (id != null) shown = await view(id);
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return (
      <Shell queued={[]} open={null}>
        <Empty title="No API">
          {error.message} Start it with <code className="font-mono text-data">docmatch serve</code>, or set DOCMATCH_API_URL.
        </Empty>
      </Shell>
    );
  }

  if (shown == null) {
    return (
      <Shell queued={queued} open={null}>
        {doc ? (
          <Empty title={`No document ${doc}`}>There is no such document in this loop run.</Empty>
        ) : (
          <Empty title="Nothing in review">Every document the loop routed has been decided.</Empty>
        )}
      </Shell>
    );
  }

  const next = queued.find((each) => each.id !== shown.id)?.id ?? null;
  return (
    <Shell queued={queued} open={shown.id}>
      <Document view={shown} next={next} />
    </Shell>
  );
}

function Shell({ queued, open, children }: { queued: Queued[]; open: number | null; children: React.ReactNode }) {
  return (
    <>
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-4 py-3">
        <p className="font-semibold">docmatch review</p>
        <nav aria-label="Documents in review" className="-mx-4 min-w-0 flex-1 px-4 max-md:overflow-x-auto md:mx-0 md:px-0">
          {queued.length ? (
            <ol className="flex gap-1 max-md:w-max md:flex-wrap">
              {queued.map((each) => (
                <li key={each.id}>
                  <Link
                    href={`/?doc=${each.id}`}
                    aria-current={each.id === open ? "page" : undefined}
                    className="flex h-8 items-center gap-2 pointer-coarse:h-11 rounded-xs border border-line px-2.5 text-data no-underline transition-colors duration-100 aria-[current=page]:border-primary aria-[current=page]:bg-primary-wash [@media(hover:hover)]:hover:bg-surface"
                  >
                    <span className="font-mono">{each.id}</span>
                    <span className="text-muted">{each.routing_reasons.join(", ")}</span>
                  </Link>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-data text-muted">Nothing in review</p>
          )}
        </nav>
      </header>
      <main className="px-4 pt-4">{children}</main>
    </>
  );
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8 flex max-w-prose flex-col gap-2 rounded-sm border border-dashed border-line-strong p-6">
      <h1 className="text-title font-semibold">{title}</h1>
      <p className="text-muted">{children}</p>
    </section>
  );
}

function Document({ view, next }: { view: View; next: number | null }) {
  const open = openItems(view);
  const fields = view.reading?.prediction.fields ?? {};
  const vendor = [fields.vendor_name].flat()[0];
  const number = [fields.document_id].flat()[0];
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
      <div className="flex min-w-0 flex-col gap-5">
        <Title view={view} vendor={vendor ?? `Document ${view.id}`} number={number ?? null} />
        {view.reading == null ? (
          <NoReading view={view} />
        ) : (
          <>
            <Header view={view} />
            <Ledger view={view} />
            <NotCompared view={view} />
          </>
        )}
        <DecideBar
          id={view.id}
          status={view.status}
          routed={view.routing_reasons.length > 0}
          summary={summary(view, open)}
          open={open}
          next={next}
        />
      </div>
      <Scan view={view} />
    </div>
  );
}

function summary(view: View, open: string[]): string {
  const parts = [];
  if (view.gate) parts.push(`gate ${view.gate.verdict}`);
  if (view.match) parts.push(`match ${view.match.verdict}`);
  parts.push(open.length ? `${open.length} open` : "nothing open");
  return parts.join(", ");
}

function Title({ view, vendor, number }: { view: View; vendor: string; number: string | null }) {
  const seconds = view.latency == null ? null : view.latency < 10 ? view.latency.toFixed(2) : view.latency.toFixed(1);
  return (
    <section className="flex flex-col gap-2 border-b border-line pb-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-headline font-semibold tracking-[-0.01em]">{vendor}</h1>
        {number && <span className="font-mono text-data text-muted">{number}</span>}
        {vendor !== `Document ${view.id}` && <span className="font-mono text-data text-muted">document {view.id}</span>}
        <StatusChip status={view.status} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {view.routing_reasons.map((reason) => {
          const standing = stillOpen(view, reason);
          return (
            <span
              key={reason}
              className={`${chip} ${standing ? "bg-hold-wash text-hold" : "bg-ok-wash text-ok"}`}
            >
              {reason}
              {standing ? "" : ", cleared"}
            </span>
          );
        })}
        <span className={quiet}>{view.backend}</span>
        <span className={quiet}>${view.cost}</span>
        {seconds != null && <span className={quiet}>{seconds} s</span>}
      </div>
    </section>
  );
}

function StatusChip({ status }: { status: View["status"] }) {
  const tone =
    status === "approved" ? "bg-ok-wash text-ok" : status === "rejected" ? "bg-hold-wash text-hold" : "bg-surface text-ink";
  return <span className={`${chip} ${tone}`}>{status.replace("_", " ")}</span>;
}

function Confidence({ value }: { value: number | null }) {
  if (value == null) return null;
  return (
    <span className="ml-1.5 font-mono text-[0.6875rem] text-muted" title="The backend's own confidence, uncalibrated">
      <span className="sr-only">confidence </span>
      {value.toFixed(2)}
    </span>
  );
}

const MONO_FIELDS = /^(amount_|date_|document_id|order_id|tax_detail_|currency_)/;

function Header({ view }: { view: View }) {
  const fields = header(view);
  const failed = new Set(
    (view.gate?.checks ?? []).filter((check) => check.outcome === "failed").flatMap((check) => check.used.map(([fieldtype]) => fieldtype)),
  );
  const tax = view.match?.findings.filter((each) => each.place.kind === "header") ?? [];
  const gate = view.gate;
  return (
    <section aria-labelledby="reading" className="flex flex-col gap-3">
      <h2 id="reading" className="text-title font-semibold">
        Header, as read
      </h2>
      <dl className="flex flex-wrap gap-px overflow-hidden rounded-sm border border-line bg-line">
        {fields.map((field) => (
          <div key={field.fieldtype} className="flex min-w-0 flex-[1_1_160px] flex-col gap-0.5 bg-bg px-3 py-2">
            <dt className={`text-label font-medium tracking-[0.04em] ${failed.has(field.fieldtype) ? "text-hold" : "text-muted"}`}>
              {field.label}
              {failed.has(field.fieldtype) && <span className="sr-only">, used by a failed gate rule</span>}
            </dt>
            {field.values.map((value, index) => (
              <dd key={index} className={`break-words ${MONO_FIELDS.test(field.fieldtype) ? "font-mono text-data" : ""}`}>
                {value.text}
                <Confidence value={value.confidence} />
              </dd>
            ))}
          </div>
        ))}
      </dl>
      {(gate || tax.length > 0) && (
        <ul className="flex flex-col gap-1 text-data">
          {gate?.checks.map((check) => (
            <li key={check.rule} className="flex flex-wrap items-baseline gap-x-2">
              <span className={check.outcome === "failed" ? "text-hold" : check.outcome === "passed" ? "text-ok" : "text-muted"}>
                Gate, {check.rule}: {check.outcome === "absent" ? "not checked" : check.outcome}
              </span>
              {check.used.length > 0 && (
                <span className="text-muted">
                  {check.used.map(([fieldtype, text]) => (
                    <span key={fieldtype} className="mr-2">
                      {label(fieldtype)} <span className="font-mono">{text}</span>
                    </span>
                  ))}
                </span>
              )}
            </li>
          ))}
          {tax.map((finding) => (
            <li key={finding.explanation} className="flex flex-wrap items-baseline gap-x-2">
              <Severity severity={finding.severity} />
              <span className="text-muted">{finding.explanation}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Severity({ severity }: { severity: "hold" | "note" }) {
  return (
    <span className={`${chip} ${severity === "hold" ? "bg-hold-wash text-hold" : "bg-note-wash text-note"}`}>{severity}</span>
  );
}

function Value({ read, ordered, align = "right" }: { read: Read | undefined; ordered?: string; align?: "left" | "right" }) {
  const unlike = ordered != null && ordered !== read?.text;
  return (
    <div className={align === "right" ? "text-right" : ""}>
      {read ? (
        <span className="font-mono text-data">
          {read.text}
          <Confidence value={read.confidence} />
        </span>
      ) : null}
      {unlike && <span className="block font-mono text-[0.6875rem] text-muted">PO {ordered}</span>}
    </div>
  );
}

function Item({ row }: { row: Row }) {
  const read = row.billed.description?.text;
  const ordered = row.ordered.description;
  return (
    <div className="min-w-0">
      <span className={read ? "" : "text-muted"}>{read ?? ordered ?? "no description"}</span>
      <Confidence value={row.billed.description?.confidence ?? null} />
      {read && ordered && ordered !== read && <span className="block text-data text-muted">PO {ordered}</span>}
      {(row.billed.code ?? row.ordered.code) && (
        <span className="block font-mono text-[0.6875rem] text-muted">{row.billed.code?.text ?? row.ordered.code}</span>
      )}
    </div>
  );
}

function Verdict({ row }: { row: Row }) {
  return (
    <div className="flex flex-col gap-1.5">
      {row.findings.map((finding) => (
        <div key={finding.explanation}>
          <Severity severity={finding.severity} /> <span className="font-medium">{finding.type}</span>
          <p className="mt-0.5 text-data text-muted">{finding.explanation.replace(`${finding.type}, `, "")}</p>
        </div>
      ))}
      {!row.findings.length && row.po != null && row.invoice != null && (
        differs(row) ? (
          <span className="text-data text-muted">no finding: within tolerance, or billed below the order</span>
        ) : (
          <span className="text-data text-ok">agrees</span>
        )
      )}
      {row.invoice != null && <Catalog resolved={row.resolved} />}
    </div>
  );
}

/** The line's catalog entry, an annotation: resolution never routes (#151). */
function Catalog({ resolved }: { resolved: Row["resolved"] }) {
  if (resolved == null) return null;
  return (
    <span className="font-mono text-[0.6875rem] text-muted">
      {resolved.sku ?? "no entry"}, score {resolved.score.toFixed(3)}
    </span>
  );
}

/** Whether any value the ledger shows for the order or the receipt reads
 * otherwise on the invoice, so "agrees" would overstate the match. */
function differs(row: Row): boolean {
  const cells = ["quantity", "unit", "unit price", "amount"] as const;
  return (
    cells.some((cell) => row.ordered[cell] != null && row.billed[cell] != null && row.ordered[cell] !== row.billed[cell]!.text) ||
    (row.received != null && row.billed.quantity != null && row.received !== row.billed.quantity.text)
  );
}

function lineName(row: Row): string {
  return row.po != null ? `PO ${row.po}` : `invoice ${row.invoice}`;
}

function Ledger({ view }: { view: View }) {
  const rows = ledger(view);
  if (view.match == null) {
    return (
      <p className="text-muted">
        The match did not run, so the lines are not set against the order. The reading is above.
      </p>
    );
  }
  const th = "px-2 py-1.5 text-left text-label font-medium tracking-[0.04em] text-muted whitespace-nowrap";
  const td = "px-2 py-2 align-top";
  return (
    <section aria-labelledby="ledger" className="flex flex-col gap-3">
      <h2 id="ledger" className="text-title font-semibold">
        Lines: order, receipt and invoice
      </h2>
      <table className="w-full border-collapse max-md:hidden">
        <thead>
          <tr className="border-b border-line-strong">
            <th className={th}>Line</th>
            <th className={th}>Item, as billed</th>
            <th className={`${th} text-right`}>Ordered</th>
            <th className={`${th} text-right`}>Received</th>
            <th className={`${th} text-right`}>Billed</th>
            <th className={th}>Unit</th>
            <th className={`${th} text-right`}>Unit price</th>
            <th className={`${th} text-right`}>Amount</th>
            <th className={`${th} w-[30%]`}>Finding and catalog</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-line">
              <td className={`${td} font-mono text-data text-muted whitespace-nowrap`}>{lineName(row)}</td>
              <td className={td}>
                <Item row={row} />
              </td>
              <td className={`${td} text-right font-mono text-data`}>{row.ordered.quantity ?? ""}</td>
              <td className={`${td} text-right font-mono text-data`}>{row.received ?? ""}</td>
              {row.invoice == null ? (
                <td className={`${td} text-muted`} colSpan={4}>
                  not billed
                </td>
              ) : (
                <>
                  <td className={td}>
                    <Value read={row.billed.quantity} />
                  </td>
                  <td className={td}>
                    <Value read={row.billed.unit} ordered={row.ordered.unit} align="left" />
                  </td>
                  <td className={td}>
                    <Value read={row.billed["unit price"]} ordered={row.ordered["unit price"]} />
                  </td>
                  <td className={td}>
                    <Value read={row.billed.amount} ordered={row.ordered.amount} />
                  </td>
                </>
              )}
              <td className={td}>
                <Verdict row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ol className="flex flex-col gap-2 md:hidden">
        {rows.map((row) => (
          <li key={row.key} className="flex flex-col gap-2 rounded-sm border border-line p-3">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-data text-muted">{lineName(row)}</span>
              <Item row={row} />
            </div>
            <dl className="grid grid-cols-3 gap-2 text-data">
              <Pair label="Ordered">{row.ordered.quantity ?? ""}</Pair>
              <Pair label="Received">{row.received ?? ""}</Pair>
              <Pair label="Billed">
                {row.invoice == null ? <span className="text-muted">not billed</span> : <Value read={row.billed.quantity} align="left" />}
              </Pair>
              {row.invoice != null &&
                (["unit", "unit price", "amount"] as const)
                  .filter((cell) => row.billed[cell] || row.ordered[cell])
                  .map((cell) => (
                    <Pair key={cell} label={cell[0].toUpperCase() + cell.slice(1)}>
                      <Value read={row.billed[cell]} ordered={row.ordered[cell]} align="left" />
                    </Pair>
                  ))}
            </dl>
            <Verdict row={row} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function Pair({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-label font-medium tracking-[0.04em] text-muted">{label}</dt>
      <dd className="font-mono">{children}</dd>
    </div>
  );
}

function NotCompared({ view }: { view: View }) {
  const skipped = view.match?.not_compared ?? [];
  if (view.match == null) return null;
  return (
    <section aria-labelledby="not-compared" className="flex flex-col gap-2">
      <h2 id="not-compared" className="text-title font-semibold">
        Not compared
      </h2>
      {skipped.length ? (
        <ul className="flex flex-col gap-1 text-data">
          {skipped.map((each, index) => (
            <li key={index} className="flex flex-wrap gap-x-2">
              <span className="font-mono text-muted">
                {placeName(each.place)}
                {each.cell ? `, ${each.cell}` : ""}
              </span>
              <span>{each.reason}</span>
              {each.texts.length > 0 && <span className="font-mono text-muted">{each.texts.join(", ")}</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-data text-muted">The matcher compared every cell it could pair.</p>
      )}
    </section>
  );
}

function NoReading({ view }: { view: View }) {
  return (
    <section className="flex flex-col gap-2 rounded-sm border border-dashed border-line-strong p-5">
      <h2 className="text-title font-semibold">No reading</h2>
      <p className="max-w-prose text-muted">
        The loop saved no reading ({view.routing_reasons.join(", ")}), so there is no ledger and nothing to correct.
        {view.status === "needs_review" ? " Decide from the scanned page alone." : ""} Spent on reading it: ${view.cost}.
      </p>
    </section>
  );
}

function Scan({ view }: { view: View }) {
  const pages = Array.from({ length: view.pages }, (_, index) => index + 1);
  // One copy per layout: pinned open beside the ledger on a wide screen, a
  // closed disclosure above it on a narrow one so the title stays in view.
  // Lazy images in a hidden copy are never fetched.
  const images = pages.map((page) => (
    // The PNG's size is the engine's to decide, so next/image's required
    // width and height are unknown here; a plain img keeps its ratio.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={page}
      src={`/api/documents/${view.id}/pages/${page}`}
      alt={`Page ${page} of ${view.pages} of document ${view.id}, as uploaded`}
      loading="lazy"
      className="w-full rounded-xs border border-line-strong"
    />
  ));
  return (
    <aside aria-label="Scanned page" className="max-lg:-order-1 lg:sticky lg:top-4 lg:max-h-[calc(100dvh-2rem)] lg:overflow-y-auto">
      <details className="lg:hidden">
        <summary className="cursor-pointer py-2 text-label font-medium tracking-[0.04em] text-muted pointer-coarse:py-3">
          {view.pages === 1 ? "The scanned page" : `The scanned pages, ${view.pages}`}
        </summary>
        <div className="flex flex-col gap-3">{images}</div>
      </details>
      <div className="flex flex-col gap-3 max-lg:hidden">{images}</div>
    </aside>
  );
}
