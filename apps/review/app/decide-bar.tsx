"use client";

import Link from "next/link";
import { useState, useTransition } from "react";

import type { Decision, Status } from "@/lib/api";

import { decide } from "./actions";

const button =
  "h-9 pointer-coarse:h-11 rounded-xs border px-3.5 text-label font-medium tracking-[0.04em] transition-[background-color,transform] duration-100 ease-out active:scale-[0.97] disabled:opacity-45 motion-reduce:transition-none";
const secondary = `${button} border-line bg-surface [@media(hover:hover)]:hover:bg-surface-2`;
const primary = `${button} border-primary bg-primary text-on-primary [@media(hover:hover)]:hover:bg-primary-text`;

/** Approve and reject, both final. Approving with something open asks first
 * and lists it; rejecting never asks (#156). */
export function DecideBar({
  id,
  status,
  routed,
  summary,
  open,
  next,
}: {
  id: number;
  status: Status;
  routed: boolean;
  summary: string;
  open: string[];
  next: number | null;
}) {
  const [pending, start] = useTransition();
  const [confirming, setConfirming] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  function send(decision: Decision) {
    start(async () => {
      setRefused(await decide(id, decision));
      setConfirming(false);
    });
  }

  const bar =
    "sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-x-4 gap-y-3 border-t border-line bg-bg px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] md:mx-0 md:rounded-sm md:border md:pb-3";

  if (status === "approved" || status === "rejected") {
    return (
      <div className={`${bar} animate-rise`} role="status">
        <p className="mr-auto">
          <span className={status === "approved" ? "text-ok" : "text-hold"}>
            {status === "approved" ? "Approved" : "Rejected"}
          </span>{" "}
          by {routed ? "the reviewer" : "the system"}. The decision is final.
        </p>
        {next != null && (
          <Link href={`/?doc=${next}`} className={`${primary} inline-flex items-center`}>
            Open document {next}
          </Link>
        )}
      </div>
    );
  }

  if (status !== "needs_review") {
    return (
      <div className={bar}>
        <p className="text-muted">The loop is still moving this document. It can be decided once it reaches review.</p>
      </div>
    );
  }

  if (confirming) {
    return (
      <div className={bar} role="alertdialog" aria-labelledby="confirm-title">
        <div className="mr-auto min-w-0 animate-rise">
          <p id="confirm-title" className="font-medium">
            Approve with {open.length === 1 ? "this" : "these"} still open?
          </p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {open.map((item) => (
              <li key={item} className="rounded-xs bg-hold-wash px-1.5 py-0.5 font-mono text-label text-hold">
                {item}
              </li>
            ))}
          </ul>
        </div>
        <div className="flex gap-2">
          <button type="button" className={secondary} onClick={() => setConfirming(false)} disabled={pending} autoFocus>
            Keep reviewing
          </button>
          <button type="button" className={primary} onClick={() => send("approved")} disabled={pending}>
            {pending ? "Approving" : "Approve anyway"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={bar}>
      <p className="mr-auto min-w-0 text-data text-muted">
        {summary}
        {refused && (
          <span className="mt-0.5 block text-hold" role="alert">
            {refused}
          </span>
        )}
      </p>
      <div className="flex gap-2">
        <button type="button" className={`${secondary} text-hold`} onClick={() => send("rejected")} disabled={pending}>
          Reject
        </button>
        <button
          type="button"
          className={primary}
          onClick={() => (open.length ? setConfirming(true) : send("approved"))}
          disabled={pending}
        >
          Approve
        </button>
      </div>
    </div>
  );
}
