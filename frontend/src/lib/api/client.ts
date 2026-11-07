// Feature 011 (Phase 5 contract proof) — typed API client for the agents family.
//
// Self-contained: imports ONLY the generated OpenAPI types (type-only, erased at build) so it can run in a
// plain Node runtime (type-stripped) for the contract smoke, with no React/store/Next imports. The caller
// (store.ts) supplies the resolved dev-auth principal — this module does not read the store, avoiding an
// import cycle and keeping it runtime-portable.
//
// Auth bridge: the mock console has no real OIDC yet, so we translate the resolved mock identity into the
// backend's dev-auth stub headers (X-Dev-Principal + X-Workspace-Id). Real OIDC replaces this later (P1).
import type { components } from "./schema";

export type AgentOut = components["schemas"]["AgentOut"];
export type AgentCreate = components["schemas"]["AgentCreate"];
type PageAgentOut = components["schemas"]["Page_AgentOut_"];

// Base URL: env override, else the local dev backend (uvicorn api.main:app on :8000).
export const API_BASE =
  (typeof process !== "undefined" && process.env && process.env.NEXT_PUBLIC_API_BASE) ||
  "http://localhost:8000";

/** Resolved caller identity for the dev-auth bridge: subject + the role held for the active workspace. */
export type DevPrincipal = { subject: string; ws: string; role: string };

export function devAuthHeaders(p: DevPrincipal): Record<string, string> {
  return {
    "X-Dev-Principal": JSON.stringify({ subject: p.subject, workspace_claims: { [p.ws]: p.role } }),
    "X-Workspace-Id": p.ws,
  };
}

async function apiFetch(path: string, principal: DevPrincipal, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...devAuthHeaders(principal), ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    // Surface the server's error envelope (no silent mock fallback — the proof needs the real path).
    let body = "";
    try {
      body = JSON.stringify(await res.json());
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`API ${res.status} ${path}${body ? `: ${body}` : ""}`);
  }
  return res;
}

export async function listAgents(principal: DevPrincipal): Promise<AgentOut[]> {
  const res = await apiFetch("/api/v1/agents", principal);
  const page = (await res.json()) as PageAgentOut;
  return page.items;
}

export async function getAgent(principal: DevPrincipal, id: string): Promise<AgentOut> {
  const res = await apiFetch(`/api/v1/agents/${encodeURIComponent(id)}`, principal);
  return (await res.json()) as AgentOut;
}

export async function createAgent(principal: DevPrincipal, body: AgentCreate): Promise<AgentOut> {
  const res = await apiFetch("/api/v1/agents", principal, { method: "POST", body: JSON.stringify(body) });
  return (await res.json()) as AgentOut;
}
