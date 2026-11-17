// Map backend DTOs ↔ the console's rich `data.ts` types, so selectors/components stay untouched (drop-in).
// One adapter pair per resource family; the store never touches a raw DTO.
//
// CONTRACT (feature 011 → 026): AgentOut now carries description/provider/model, the resolved draft, draft_dirty,
// restore_status and the version embed (created_by/is_active). It still does NOT carry owner/tags or the 30-day
// metrics the console renders — those stay safe placeholders until a metrics rollup exists.
import type {
  Agent, AgentConfig, ApiToken, InputVariable, InputVariableType, LiveDraft, ModelRef, OutputFormat, Version,
} from "../data";
import type { AccessPolicy } from "../access-policy";
import type { Role } from "../permissions";
import type { AgentCreate, AgentOut, DraftIn, DraftOut, TokenOut, VersionOut } from "./client";

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
// Backend capability-profile provider keys are lowercase; the console renders the capitalized ModelRef literal.
const PROVIDER_DISPLAY: Record<string, ModelRef["provider"]> = {
  anthropic: "Anthropic", openai: "OpenAI", google: "Google", mistral: "Mistral", meta: "Meta",
};
export const providerDisplay = (p: string | null | undefined): Agent["provider"] =>
  (p ? PROVIDER_DISPLAY[p] ?? (p as Agent["provider"]) : ("" as Agent["provider"]));
export const providerKey = (p: string): string => p.toLowerCase();

const VAR_TYPES: readonly InputVariableType[] = ["string", "number", "boolean", "enum"];

/** io_contract.input_schema (JSON Schema object) → the console's typed input variables. */
export function inputVariablesFromSchema(schema: unknown): InputVariable[] {
  const s = (schema ?? {}) as { properties?: Record<string, Record<string, unknown>>; required?: string[] };
  const required = new Set(s.required ?? []);
  return Object.entries(s.properties ?? {}).map(([name, p]) => {
    const raw = Array.isArray(p.enum) ? "enum" : p.type === "integer" ? "number" : String(p.type ?? "string");
    const type = (VAR_TYPES as readonly string[]).includes(raw) ? (raw as InputVariableType) : "string";
    return { name, type, required: required.has(name), ...(p.default != null ? { default: String(p.default) } : {}) };
  });
}

/** Typed input variables → input_schema. Keys of an existing property the console doesn't model (enum values,
 *  description, …) are carried over from `prev` so a round-trip never silently drops them. */
export function schemaFromInputVariables(vars: InputVariable[], prev: unknown): Record<string, unknown> {
  // Top-level keys the console doesn't model (additionalProperties, $schema, description, …) survive the round-trip.
  const top = { ...((prev ?? {}) as Record<string, unknown>) };
  const prevProps = (top.properties ?? {}) as Record<string, Record<string, unknown>>;
  const properties: Record<string, Record<string, unknown>> = {};
  for (const v of vars) {
    const base = { ...(prevProps[v.name] ?? {}) };
    delete base.default;
    if (v.type === "enum") {
      base.type = "string";
      // `enum: []` is an unsatisfiable schema (no value validates) — seed from the default, else leave it out.
      if (!Array.isArray(base.enum) || base.enum.length === 0) {
        if (v.default) base.enum = [v.default];
        else delete base.enum;
      }
    } else {
      // The console folds integer into "number" on read; keep the stricter integer when the user didn't change type.
      base.type = v.type === "number" && base.type === "integer" ? "integer" : v.type;
      delete base.enum;
    }
    if (v.default) {
      const n = Number(v.default);
      base.default = v.type === "number" && Number.isFinite(n) ? n : v.default;
    }
    properties[v.name] = base;
  }
  return { ...top, type: "object", properties, required: vars.filter((v) => v.required).map((v) => v.name) };
}

function draftOut(d: DraftOut | null | undefined, o: AgentOut): LiveDraft | undefined {
  if (!d) return undefined;
  return {
    provider: d.provider ?? o.provider ?? "",
    model: d.model ?? o.model ?? "",
    model_params: { ...(d.model_params ?? {}) },
    system_prompt: d.system_prompt ?? "",
    io_contract: { ...(d.io_contract ?? {}) },
    kb_ids: d.kb_ids ?? [],
    tool_ids: d.tool_ids ?? [],
    skill_ids: d.skill_ids ?? [],
  };
}

/** VersionOut → the console Version. VersionOut carries no per-version model/eval verdict/note: those render empty
 *  rather than invented (`status` is a neutral placeholder the live Versions list doesn't display). */
export function versionOutToVersion(v: VersionOut): Version {
  return {
    id: v.id, isActive: v.is_active, label: v.label, status: "draft", model: "", provider: "",
    createdAt: v.created_at, createdBy: v.created_by ?? "", evalVerdict: "none", note: "",
    inputVariables: inputVariablesFromSchema((v.io_contract as { input_schema?: unknown } | undefined)?.input_schema),
  };
}

/** The draft-config slice of AgentConfig the server owns (prompt, params, io contract). Everything else in
 *  AgentConfig is console-only and falls back to defaults via resolveConfig. */
function configFromDraft(d: LiveDraft | undefined): Partial<AgentConfig> | undefined {
  if (!d) return undefined;
  const mp = d.model_params;
  const io = d.io_contract as { input_schema?: unknown; output_format?: unknown };
  return {
    systemPrompt: d.system_prompt,
    ...(typeof mp.temperature === "number" ? { temperature: mp.temperature } : {}),
    ...(typeof mp.max_tokens === "number" ? { maxOutputTokens: mp.max_tokens } : {}),
    inputVariables: inputVariablesFromSchema(io.input_schema),
    ...(io.output_format === "json" || io.output_format === "text" ? { outputFormat: io.output_format } : {}),
  };
}

/** The draft fields the console can edit live. Anything else in the draft is round-tripped untouched. */
export type DraftEdit = {
  provider?: string; model?: string; systemPrompt?: string; temperature?: number; maxOutputTokens?: number;
  inputVariables?: InputVariable[]; outputFormat?: OutputFormat;
};

/** Build the PATCH `draft` body: the server's current draft with only the edited fields replaced (pure). */
export function draftWithEdit(d: LiveDraft, e: DraftEdit): DraftIn {
  const model_params = { ...d.model_params };
  if (e.temperature !== undefined) model_params.temperature = e.temperature;
  if (e.maxOutputTokens !== undefined) model_params.max_tokens = Math.round(e.maxOutputTokens);
  const io_contract = { ...d.io_contract };
  if (e.inputVariables !== undefined) io_contract.input_schema = schemaFromInputVariables(e.inputVariables, d.io_contract.input_schema);
  if (e.outputFormat !== undefined) io_contract.output_format = e.outputFormat;
  return {
    provider: e.provider !== undefined ? providerKey(e.provider) : d.provider,
    model: e.model ?? d.model,
    model_params,
    system_prompt: e.systemPrompt ?? d.system_prompt,
    io_contract,
    kb_ids: d.kb_ids, tool_ids: d.tool_ids, skill_ids: d.skill_ids,
  };
}

export function agentOutToAgent(o: AgentOut, consoleWorkspaceId: string): Agent {
  const active = o.versions?.find((v) => v.id === o.active_version_id);
  const draft = draftOut(o.draft, o);
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
    description: o.description ?? "",
    provider: providerDisplay(draft?.provider ?? o.provider),
    model: draft?.model ?? o.model ?? "",
    draftDirty: o.draft_dirty,
    restoreStatus: o.restore_status ?? null,
    activeVersionId: o.active_version_id ?? undefined,
    versionList: (o.versions ?? []).map(versionOutToVersion),
    draft,
    restrictedBindings: o.draft?.restricted_bindings ?? 0,
    config: configFromDraft(draft),
    // --- placeholders for fields AgentOut doesn't carry (see finding above) ---
    // TODO(backend/contract): metrics from a runs rollup; owner/tags have no server field yet.
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

/** Backend TokenOut (masked) → console ApiToken. `lastUsed` is "" when never used (the UI renders "Never"). */
export function tokenOutToApiToken(t: TokenOut, agentName: string): ApiToken {
  return {
    id: t.id, name: t.name, agent: agentName, agentId: t.agent_id, prefix: `${t.prefix}…`, created: t.created_at,
    lastUsed: t.last_used_at ?? "", scopes: "invoke", expires: t.expires_at ?? undefined,
  };
}
