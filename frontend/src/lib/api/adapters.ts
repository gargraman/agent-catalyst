// Map backend DTOs ↔ the console's rich `data.ts` types, so selectors/components stay untouched (drop-in).
// One adapter pair per resource family; the store never touches a raw DTO.
//
// CONTRACT FINDING (feature 011): AgentOut is a thin projection — it does NOT carry description/provider/model,
// owner/tags, or the 30-day metrics the console renders; those live on the version/metrics endpoints. Until the
// backend enriches AgentOut (or the FE also fetches version + metrics), those fields are safe placeholders.
import type { Agent } from "../data";
import type { AccessPolicy } from "../access-policy";
import type { Role } from "../permissions";
import type { AgentCreate, AgentOut } from "./client";

function accessPolicyOut(p: AgentOut["access_policy"]): AccessPolicy {
  return {
    visibility: p.visibility,
    // The server validates role names; the schema types them as plain strings.
    useRoles: p.useRoles as Role[],
    useTeams: p.useTeams,
    manageRoles: p.manageRoles as Role[],
    manageTeams: p.manageTeams,
    ...(p.owner ? { owner: p.owner } : {}),
  };
}

/**
 * @param consoleWorkspaceId the console's id for the workspace the agent was fetched under. AgentOut.workspace_id
 *   is the backend UUID; the store's workspace filter keys on the console id (slug) until every family is wired
 *   and the console switches to UUID ids outright (feature 011 F-D — see Workspace.backendId in iam.ts).
 */
export function agentOutToAgent(o: AgentOut, consoleWorkspaceId: string): Agent {
  const active = o.versions?.find((v) => v.id === o.active_version_id);
  return {
    id: o.id,
    name: o.name,
    status: o.status, // AgentOut.status union == AgentStatus
    workspaceId: consoleWorkspaceId,
    createdAt: o.created_at,
    updatedAt: o.created_at,
    activeVersion: active?.label ?? "",
    versions: o.versions?.length ?? 0,
    accessPolicy: accessPolicyOut(o.access_policy),
    // --- placeholders for fields AgentOut doesn't carry (see finding above) ---
    // TODO(backend/contract): replace with real values once AgentOut projects them (services/api/schemas/agents.py
    //   TODO): provider/model from the active version first; metrics from a runs rollup. Note `provider` here is a
    //   fake "Anthropic" — the provider filter on the agents page groups every live agent under it.
    description: "",
    provider: "Anthropic",
    model: "",
    owner: "",
    tags: [],
    calls30d: 0,
    cost30d: 0,
    p95ms: 0,
    evalPass: 0,
    drift: "none",
    callsSpark: [],
    knowledgeBases: [],
    tools: [],
    skills: [],
  };
}

// The create contract requires a well-formed io_contract.input_schema (surfaced by the live smoke — a bare
// body is rejected 400 input.invalid). Derive a minimal valid contract from the console's draft agent.
export function agentToCreate(a: Agent): AgentCreate {
  return {
    name: a.name,
    // CONTRACT FINDING: the backend capability-profile provider key is lowercase ("anthropic"), but the
    // console's provider literal is capitalized ("Anthropic") — send the normalized (lowercased) value or
    // the create is rejected 400 "unknown provider/model".
    provider: (a.provider || "Anthropic").toLowerCase(),
    model: a.model || "claude-sonnet-5-5",
    system_prompt: a.config?.systemPrompt ?? "",
    io_contract: {
      input_schema: { type: "object", properties: { message: { type: "string" } }, required: ["message"] },
      output_format: "text",
      output_schema: null,
    },
    // The form's chosen policy; omitted → the server applies its default ("workspace").
    ...(a.accessPolicy ? { access_policy: { ...a.accessPolicy } } : {}),
  };
}
