// Feature 011 (Phase 5) — map the backend's thin AgentOut projection ↔ the console's rich `Agent` type,
// so selectors/components stay untouched (drop-in). CONTRACT FINDING surfaced by this proof: AgentOut does
// NOT carry provider/model or the 30-day metrics the console renders — those live on the version/metrics
// endpoints. Until the backend enriches AgentOut (or the FE also fetches the version + metrics), the mapped
// Agent fills those with safe placeholders. Documented in specs/011-fe-contract-proof.
import type { Agent } from "../data";
import type { AgentCreate, AgentOut } from "./client";

export function agentOutToAgent(o: AgentOut): Agent {
  return {
    id: o.id,
    name: o.name,
    status: o.status, // AgentOut.status union == AgentStatus
    workspaceId: o.workspace_id,
    createdAt: o.created_at,
    updatedAt: o.created_at,
    activeVersion: o.active_version_id ?? "",
    versions: o.versions?.length ?? 0,
    // --- placeholders for fields AgentOut doesn't carry (see finding above) ---
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
    system_prompt: "",
    io_contract: {
      input_schema: { type: "object", properties: { message: { type: "string" } }, required: ["message"] },
      output_format: "text",
      output_schema: null,
    },
  };
}
