"use server";

import { refresh } from "next/cache";

import { decide as post, edit, type Change, type Decision } from "@/lib/api";

/** One reviewer decision, final; the API's refusal as a sentence, or null. */
export async function decide(id: number, decision: Decision): Promise<string | null> {
  if (!Number.isInteger(id) || (decision !== "approved" && decision !== "rejected")) {
    return "That is not a decision.";
  }
  const refused = await post(id, decision);
  refresh();
  return refused;
}

/** One edit to the reading, rerun by the engine; its refusal, or null. */
export async function correct(id: number, change: Change): Promise<string | null> {
  if (!Number.isInteger(id)) return "That is not a document.";
  const refused = await edit(id, change);
  refresh();
  return refused;
}
