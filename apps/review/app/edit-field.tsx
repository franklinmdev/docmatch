"use client";

import { useId, useState, useTransition } from "react";

import type { Change } from "@/lib/api";

import { correct } from "./actions";

type Target = { kind: "header"; fieldtype: string } | { kind: "cell"; line: number; fieldtype: string };

/** One value of the reading, edited in place: it commits on blur or Enter,
 * Escape puts back what was there, and the engine reruns the gate,
 * resolution and match before the page redraws (#156). Under it, the value
 * as read, struck through, once a correction changed it. */
export function EditField({
  id,
  target,
  value,
  was,
  label,
  align = "left",
  mono = true,
}: {
  id: number;
  target: Target;
  value: string;
  was: string | null | undefined;
  label: string;
  align?: "left" | "right";
  mono?: boolean;
}) {
  const [pending, start] = useTransition();
  const [refused, setRefused] = useState<string | null>(null);
  const error = useId();

  function commit(input: HTMLInputElement) {
    const left = input.value.trim();
    if (left === value) {
      input.value = value;
      return;
    }
    const change: Change = { ...target, value: left };
    start(async () => setRefused(await correct(id, change)));
  }

  return (
    <div className={`flex min-w-0 flex-col gap-0.5 ${align === "right" ? "items-end" : ""}`}>
      <input
        // A new value from the engine remounts the field with it.
        key={value}
        defaultValue={value}
        aria-label={label}
        aria-busy={pending || undefined}
        aria-invalid={refused ? true : undefined}
        aria-describedby={refused ? error : undefined}
        size={Math.max(4, value.length + 1)}
        placeholder="not read"
        spellCheck={false}
        autoComplete="off"
        onBlur={(event) => commit(event.currentTarget)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            event.currentTarget.value = value;
            event.currentTarget.blur();
          }
        }}
        className={`h-8 max-w-full min-w-0 rounded-sm border border-line bg-bg px-1.5 transition-[border-color,opacity] duration-100 ease-out placeholder:text-muted focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 aria-busy:opacity-60 aria-invalid:border-hold pointer-coarse:h-11 max-md:text-base [@media(hover:hover)]:hover:border-line-strong ${
          mono ? "font-mono text-data" : ""
        } ${align === "right" ? "text-right" : ""}`}
      />
      <Was text={was} />
      {refused && (
        <span id={error} role="alert" className="text-data text-hold">
          {refused}
        </span>
      )}
    </div>
  );
}

/** The value as read, struck through, under a corrected one. */
export function Was({ text }: { text: string | null | undefined }) {
  if (text == null) return null;
  return (
    <s className="font-mono text-[0.6875rem] text-muted decoration-muted">
      <span className="sr-only">read as </span>
      {text || "not read"}
    </s>
  );
}

/** A line removed, restored or added: one click, saved and rerun. */
export function LineButton({ id, change, children }: { id: number; change: Change; children: React.ReactNode }) {
  const [pending, start] = useTransition();
  const [refused, setRefused] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={pending}
        onClick={() => start(async () => setRefused(await correct(id, change)))}
        className="inline-flex h-7 items-center rounded-xs px-1.5 text-label font-medium tracking-[0.04em] text-primary-text underline-offset-3 transition-[background-color,transform] duration-100 ease-out active:scale-[0.97] disabled:opacity-45 motion-reduce:transition-none pointer-coarse:h-11 [@media(hover:hover)]:hover:bg-primary-wash"
      >
        {children}
      </button>
      {refused && (
        <span role="alert" className="text-data text-hold">
          {refused}
        </span>
      )}
    </span>
  );
}
