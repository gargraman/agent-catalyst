// Refusal → console message table (feature 026, research D16; FR-007 / SC-005).
//
// Pure: no React, no store. Every live seam renders a server refusal through `refusalMessage` so the seven
// playground refusals (and the 409/404 edit refusals) each read distinctly. An unknown code falls back to the
// server's own `message` verbatim — never a generic "something went wrong" that hides what the server said.
// A pre-stream refusal message never implies a provider call happened.
import type { ApiError } from "./client";

export type Refusal = { title: string; body: string; retryable: boolean };

const BINDING_NOUN: Record<string, string> = {
  knowledge_base: "knowledge base",
  tool: "tool",
  skill: "skill",
};

function usd(v: unknown): string | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : null;
}

/** Map a typed API (or stream `error` frame) refusal to a distinct console message. Stream frames are surfaced as
 *  an `ApiError` with status 0 and the frame's `code` (see `stream.ts`). */
export function refusalMessage(err: Pick<ApiError, "status" | "code" | "message" | "detail" | "retryAfter">): Refusal {
  const d = err.detail ?? {};
  const binding = typeof d.binding === "string" ? d.binding : undefined;

  if (binding && (err.status === 404 || err.code === "not_found")) {
    const noun = BINDING_NOUN[binding] ?? "bound resource";
    return {
      title: `A bound ${noun} no longer exists`,
      body: `This draft references a ${noun} that was deleted or is not in this workspace. Remove it from the draft and try again.`,
      retryable: false,
    };
  }

  switch (err.code) {
    case "budget.exceeded": {
      const used = usd(d.month_to_date_usd);
      const cap = usd(d.cap_usd);
      return {
        title: "Monthly spend cap reached",
        body: used && cap ? `Monthly spend cap reached — ${used} of ${cap} used. Nothing was sent to the model.`
          : "Monthly spend cap reached. Nothing was sent to the model.",
        retryable: false,
      };
    }
    case "rate.limited":
      return {
        title: "Too many requests",
        body: err.retryAfter != null ? `Too many requests — retry in ${Math.ceil(err.retryAfter)} s.`
          : "Too many requests — retry shortly.",
        retryable: true,
      };
    case "access.denied":
      return { title: "Not permitted", body: "You don't have permission to do this. Ask a workspace admin for access.", retryable: false };
    case "not_found":
      return { title: "Not available", body: "This agent or version is no longer available (or you don't have access to it).", retryable: false };
    case "provider.unavailable":
      return { title: "Provider unavailable", body: "The model provider is temporarily unavailable. Try again in a moment.", retryable: true };
    case "config_error":
      return { title: "Configuration error", body: "No provider key is configured for this workspace, or the model isn't available. An admin can fix this in Settings.", retryable: false };
    case "timeout":
      return { title: "Stopped: took too long", body: "The answer took too long and was stopped.", retryable: true };
    case "auth.required":
      return { title: "Access was revoked", body: "Your access was revoked while this answer was streaming.", retryable: false };
    case "conflict":
    case "conflict.stale":
      return { title: "Changed by someone else", body: "This agent was changed by someone else since you opened it. Reload to see the latest, then re-apply your edit.", retryable: false };
    case "agent.archived":
      return { title: "Agent is archived", body: "This agent is archived. Restore it to run or edit it.", retryable: false };
    case "eval.suite_has_runs":
      return { title: "Suite has eval runs", body: "This suite has eval runs that are kept as evidence, so it can't be deleted.", retryable: false };
    case "version.frozen":
      return { title: "Version is frozen", body: "This version is committed and its bindings are immutable — create a new version instead.", retryable: false };
    case "input.invalid":
      return { title: "Invalid input", body: err.message, retryable: false };
  }

  if (err.status === 403) return { title: "Not permitted", body: err.message, retryable: false };
  if (err.status === 404) return { title: "Not available", body: err.message, retryable: false };
  return { title: "Request refused", body: err.message, retryable: err.status >= 500 || err.status === 0 };
}
