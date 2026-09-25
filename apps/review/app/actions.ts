"use server";

import { refresh } from "next/cache";

import { decide as post, type Decision } from "@/lib/api";

/** One reviewer decision, final; the API's refusal as a sentence, or null. */
export async function decide(id: number, decision: Decision): Promise<string | null> {
  if (!Number.isInteger(id) || (decision !== "approved" && decision !== "rejected")) {
    return "That is not a decision.";
  }
  const refused = await post(id, decision);
  refresh();
  return refused;
}
