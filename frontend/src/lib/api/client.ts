// Typed API client (feature 011 contract proof → live wiring).
//
// Self-contained: imports ONLY the generated OpenAPI types (type-only, erased at build) so it can run in a
// plain Node runtime (type-stripped) for the contract smoke, with no React/store/Next imports. The caller
// (store.ts) supplies the resolved caller — this module does not read the store, avoiding an import cycle and
// keeping it runtime-portable.
//
// Auth bridge: the console has no real OIDC yet, so the mock identity is translated into the backend's dev-auth
// stub headers (X-Dev-Principal + X-Workspace-Id). This is the ONE place auth is attached — real OIDC (cookie
// session, feature 019) replaces `authHeaders` and nothing else changes.
import type { components } from "./schema";

export type AgentOut = components["schemas"]["AgentOut"];
export type AgentCreate = components["schemas"]["AgentCreate"];
export type WorkspaceMembershipOut = components["schemas"]["WorkspaceMembershipOut"];
type PageAgentOut = components["schemas"]["Page_AgentOut_"];

/** Live mode: when "live", the store's wired families read/write the real backend; otherwise the console is
 *  pure mock (seed fixtures) exactly as before. Inlined at build time (NEXT_PUBLIC_*), so it is a build switch. */
export const LIVE_API = process.env.NEXT_PUBLIC_API_MODE === "live";

// Base URL. In the browser the default is SAME-ORIGIN ("") — `/api/v1/*` is proxied to the backend by the Next
// rewrite in next.config.ts, so there is no CORS surface and the future cookie session + CSRF (feature 019) work
// unchanged. Outside the browser (the Node contract smoke) default to the local dev backend.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? (typeof window === "undefined" ? "http://localhost:8000" : "");

/** Resolved caller for the dev-auth bridge.
 *  - `ws` is the BACKEND workspace UUID (not the console slug — feature 011 finding F-D). Omit it for the
 *    pre-selection discovery call.
 *  - `role` is optional and should normally be OMITTED: with no claim, the backend resolves the caller's role from
 *    its members store (feature 012), so the server — not the console — is authoritative. Pass it only to
 *    simulate a claim-carrying token (contract smoke). */
export type DevPrincipal = { subject: string; ws?: string; role?: string };

export function devAuthHeaders(p: DevPrincipal): Record<string, string> {
  const claims = p.ws && p.role ? { [p.ws]: p.role } : {};
  const h: Record<string, string> = {
    "X-Dev-Principal": JSON.stringify({ subject: p.subject, workspace_claims: claims }),
  };
  if (p.ws) h["X-Workspace-Id"] = p.ws;
  return h;
}

/** A non-2xx response, carrying the backend's typed error envelope ({"error": {code, message, detail?}}) so the
 *  UI can branch on `status`/`code` (403 access.denied vs 5xx) instead of parsing a string. */
export class ApiError extends Error {
  // Explicit fields, not TS parameter properties: this module must stay runnable under Node's strip-only
  // type stripping (the contract smoke), which rejects parameter properties.
  readonly status: number;
  readonly code: string;
  readonly path: string;
  constructor(status: number, code: string, message: string, path: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.path = path;
  }
}

// TODO(auth): when the 019 cookie session replaces dev headers, state-changing requests (POST/PATCH/PUT/DELETE)
//   must send `X-CSRF-Token` = the readable `ac_csrf` cookie, or the API answers 403 csrf.invalid (api/csrf.py) —
//   add it here, the single choke point. Also send `credentials: "same-origin"` (default for same-origin fetch).
// TODO(fe-wiring): loaders don't cancel on unmount / workspace switch — thread a caller AbortSignal through (combine
//   with the timeout via AbortSignal.any) once a family needs it. Harmless today: results key by workspace.
/** Per-request ceiling, so a hung backend surfaces as an error (→ Retry) instead of an eternal "loading". */
const REQUEST_TIMEOUT_MS = 15_000;

async function apiFetch(path: string, principal: DevPrincipal, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: { "Content-Type": "application/json", ...devAuthHeaders(principal), ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    // Surface the server's error envelope (no silent mock fallback).
    let code = "http.error";
    let message = `API ${res.status} ${path}`;
    try {
      const body = (await res.json()) as { error?: { code?: string; message?: string } };
      if (body?.error?.code) code = body.error.code;
      if (body?.error?.message) message = body.error.message;
    } catch {
      /* non-JSON error body (e.g. proxy 502 when the backend is down) */
    }
    throw new ApiError(res.status, code, message, path);
  }
  return res;
}

/** Workspaces the caller may enter + the role held in each (pre-selection; no X-Workspace-Id). */
export async function listMyWorkspaces(principal: DevPrincipal): Promise<WorkspaceMembershipOut[]> {
  const res = await apiFetch("/api/v1/workspaces", { subject: principal.subject });
  return (await res.json()) as WorkspaceMembershipOut[];
}

/** Hard ceiling on cursor pages followed in one list call — a runaway/looping cursor fails loud, not forever. */
const MAX_PAGES = 50;

/** Every agent in the workspace. Follows `next_cursor` to the end — reading only page 1 would silently truncate
 *  the list once a workspace outgrows the server's page size. Callers that need lazy/infinite scroll later can
 *  page explicitly; the store contract (a full workspace list) doesn't change. */
export async function listAgents(principal: DevPrincipal): Promise<AgentOut[]> {
  const items: AgentOut[] = [];
  let cursor: string | null | undefined;
  for (let i = 0; i < MAX_PAGES; i++) {
    const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    const res = await apiFetch(`/api/v1/agents${qs}`, principal);
    const page = (await res.json()) as PageAgentOut;
    items.push(...page.items);
    cursor = page.next_cursor;
    if (!cursor) return items;
  }
  throw new ApiError(0, "client.pagination", `agent list exceeded ${MAX_PAGES} pages`, "/api/v1/agents");
}

export async function getAgent(principal: DevPrincipal, id: string): Promise<AgentOut> {
  const res = await apiFetch(`/api/v1/agents/${encodeURIComponent(id)}`, principal);
  return (await res.json()) as AgentOut;
}

export async function createAgent(
  principal: DevPrincipal, body: AgentCreate, opts: { idempotencyKey?: string } = {},
): Promise<AgentOut> {
  const headers: Record<string, string> = opts.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : {};
  const res = await apiFetch("/api/v1/agents", principal, { method: "POST", body: JSON.stringify(body), headers });
  return (await res.json()) as AgentOut;
}
