/* ============================================================
   Agent Catalyst - mock data (sensible, internally consistent)
   No real secrets. Numbers are illustrative sample data.
   ============================================================ */

/* @backend remove - every exported seed array (AGENTS, KNOWLEDGE_BASES, INGESTION_JOBS, MCP_SERVERS,
   TOOLS_REPO, EVAL_RUNS, RUNS, API_TOKENS, PROVIDER_KEYS, MEMBERS, MODELS) and the get* helpers plus
   the chart series (ACTIVITY, RUNS_SERIES, SPEND_SERIES, LATENCY_SERIES) are mock fixtures. The
   server becomes the source of truth and the store reads the API (see @backend markers in store.ts);
   keep ONLY the exported TYPES, which define the API response shapes. Chart series become a metrics
   API read. [ref: backend-integration-plan.md#part-2] */

import type { AccessPolicy } from "./access-policy";
import type { Connector } from "./connectors";
import type { ChannelPlatform } from "./channels";

export type AgentStatus = "published" | "draft" | "archived" | "eval_failing";
export type Verdict = "pass" | "fail" | "low_confidence";

export type ModelRef = {
  provider: "Anthropic" | "OpenAI" | "Google" | "Mistral" | "Meta";
  model: string;
  inUsd: number; // per 1M input tokens
  outUsd: number; // per 1M output tokens
  context: string;
};

export const MODELS: ModelRef[] = [
  { provider: "Anthropic", model: "claude-opus-5-5", inUsd: 4, outUsd: 20, context: "1M" },
  { provider: "Anthropic", model: "claude-sonnet-5-5", inUsd: 2, outUsd: 10, context: "1M" },
  { provider: "Anthropic", model: "claude-haiku-4-5", inUsd: 1, outUsd: 5, context: "200K" },
  { provider: "OpenAI", model: "gpt-5", inUsd: 5, outUsd: 20, context: "400K" },
  { provider: "OpenAI", model: "gpt-5-mini", inUsd: 0.6, outUsd: 2.4, context: "400K" },
  { provider: "Google", model: "gemini-2.5-pro", inUsd: 2.5, outUsd: 15, context: "1M" },
  { provider: "Mistral", model: "mistral-large-2", inUsd: 2, outUsd: 6, context: "128K" },
  { provider: "Meta", model: "llama-3.3-70b (self-host)", inUsd: 0, outUsd: 0, context: "128K" },
];

export type Member = { name: string; email: string; role: "Admin" | "Publisher" | "Builder" | "Viewer"; lastActive: string };
export const MEMBERS: Member[] = [
  { name: "Raman Garg", email: "raman.garg@trellix.com", role: "Admin", lastActive: "2026-10-04T09:12:00Z" },
  { name: "Priya Nair", email: "priya.nair@trellix.com", role: "Publisher", lastActive: "2026-10-04T07:40:00Z" },
  { name: "Diego Fuentes", email: "diego.fuentes@trellix.com", role: "Builder", lastActive: "2026-10-03T18:22:00Z" },
  { name: "Mei-Ling Chen", email: "meiling.chen@trellix.com", role: "Builder", lastActive: "2026-10-03T14:05:00Z" },
  { name: "Omar Haddad", email: "omar.haddad@trellix.com", role: "Viewer", lastActive: "2026-10-01T11:48:00Z" },
  { name: "Sofia Rossi", email: "sofia.rossi@trellix.com", role: "Publisher", lastActive: "2026-10-02T16:30:00Z" },
];

export type Version = {
  label: string;
  status: "published" | "evaluated" | "draft" | "deprecated";
  model: string;
  provider: string;
  createdAt: string;
  createdBy: string;
  evalVerdict: Verdict | "none";
  note: string;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  provider: ModelRef["provider"];
  model: string;
  owner: string;
  createdAt: string;
  updatedAt: string;
  activeVersion: string;
  versions: number;
  tags: string[];
  // metrics (last 30 days)
  calls30d: number;
  cost30d: number;
  p95ms: number;
  evalPass: number; // %
  drift: "stable" | "watch" | "regressed" | "none";
  callsSpark: number[];
  knowledgeBases: string[];
  tools: string[];
  endpoint?: string;
  workspaceId?: string;
  accessPolicy?: AccessPolicy;
  // Mutable draft config (model params, prompt, memory, retrieval, guardrails, spend).
  // Mirrors the fields the backend freezes into an immutable version snapshot on publish
  // (agent_versions.model_config / system_prompt / memory_config / context_config /
  // guardrail_config — see docs/backend/01-data-model.md). Partial so seeds fall back to
  // DEFAULT_AGENT_CONFIG via resolveConfig().
  config?: Partial<AgentConfig>;
};

/* ---- Agent configuration (the editable draft config) ---- */
export type ReasoningEffort = "low" | "medium" | "high" | "xhigh" | "max";
export type OutputFormat = "text" | "json";
export type TruncationStrategy = "summarize" | "drop_oldest";
export type InjectionStrategy = "tool" | "stuff";
export type Reranker = "bge" | "none";
export type GuardrailStage = { name: string; desc: string; on: boolean };

export type AgentConfig = {
  // model params
  temperature: number;
  maxOutputTokens: number;
  reasoningEffort: ReasoningEffort;
  fallbackModel: string;
  // prompt
  systemPrompt: string;
  // I/O contract
  outputFormat: OutputFormat;
  includeCitations: boolean;
  // context & memory — PLANNED: captured as config, not enforced at runtime yet.
  // See docs/session-memory-status.md.
  truncationStrategy: TruncationStrategy;
  sessionMemory: boolean;
  maxTurnsRetained: number;
  longTermMemory: boolean;
  // retrieval (Knowledge tab)
  retrieval: {
    injection: InjectionStrategy;
    topK: number;
    similarityThreshold: number;
    reranker: Reranker;
    maxInjectedTokens: number;
    hybrid: boolean;
  };
  // guardrails (Guardrails tab)
  guardrails: { input: GuardrailStage[]; output: GuardrailStage[] };
  // spend controls
  spend: { monthlyCapUsd: number; maxConcurrency: number; timeoutSec: number };
};

// Per-agent system prompts (seed). Falls back to a generic prompt in resolveConfig().
export const PROMPTS: Record<string, string> = {
  "triage-copilot": "You are a SOC triage analyst. Given an XDR alert, correlate the signals, assess severity, and produce an analyst-ready verdict. Always cite the playbook sections you relied on. If evidence is insufficient, say so and request the specific telemetry you need. Never fabricate indicators.",
  "docs-assistant": "You are a product documentation assistant. Answer only from the retrieved documentation and always include citations to the source pages. If the docs do not cover the question, say you do not know and point to the closest relevant section.",
  "benefits-qa": "You are an HR benefits assistant. Answer employee questions about health insurance, 401(k), and other benefits using only the retrieved benefits guide and handbook, and always cite the source. If something is unclear or not covered, say so and point the employee to HR. Never give personal legal, tax, or medical advice.",
  "pto-helper": "You are a time-off assistant. Answer PTO and leave-policy questions from the handbook, and when asked about a specific balance, call the PTO-balance tool and report the result. If a request needs manager or HR approval, say so and explain the next step.",
  "onboarding-concierge": "You are an onboarding concierge for new hires. Use the employee handbook and the new hire's start details to guide them through first-week tasks, policies, and setup. Be welcoming and concise, and hand off to a human for anything you cannot confirm.",
  "policy-assistant": "You are an HR policy assistant. Answer policy and code-of-conduct questions grounded in the policy library, always citing the relevant policy. Flag low-confidence answers and escalate sensitive matters (harassment, grievances) to an HR representative rather than advising directly.",
  "resume-screener": "You are a recruiting assistant. Screen applications against the provided role rubric only, and draft a neutral shortlist rationale citing the rubric criteria. Do not consider or infer protected characteristics. Surface uncertainty rather than guessing.",
};

const DEFAULT_PROMPT = "You are a helpful, precise internal assistant. Be concise and ground every answer in the provided context and tools. If you are unsure, say so.";

export const DEFAULT_GUARDRAILS: AgentConfig["guardrails"] = {
  input: [
    { name: "PII detection", desc: "Redact emails, tokens and account IDs", on: true },
    { name: "Prompt-injection heuristics", desc: "Flag instruction-override attempts", on: true },
    { name: "Topic allow-list", desc: "Security and product topics only", on: true },
    { name: "Max input length", desc: "Reject inputs over 24k tokens", on: false },
  ],
  output: [
    { name: "PII redaction", desc: "Scrub sensitive values before flush", on: true },
    { name: "Profanity filter", desc: "Hardened profanity handling", on: true },
    { name: "Citation required", desc: "Block answers without a source", on: true },
    { name: "LLM policy judge", desc: "Final policy check (adds latency)", on: false },
  ],
};

export const DEFAULT_AGENT_CONFIG: Omit<AgentConfig, "systemPrompt"> = {
  temperature: 0.2,
  maxOutputTokens: 4096,
  reasoningEffort: "high",
  fallbackModel: "claude-sonnet-5-5",
  outputFormat: "json",
  includeCitations: true,
  truncationStrategy: "summarize",
  sessionMemory: true,
  maxTurnsRetained: 24,
  longTermMemory: false,
  retrieval: { injection: "tool", topK: 6, similarityThreshold: 0.72, reranker: "bge", maxInjectedTokens: 4000, hybrid: true },
  guardrails: DEFAULT_GUARDRAILS,
  spend: { monthlyCapUsd: 2000, maxConcurrency: 25, timeoutSec: 30 },
};

// Parse a numeric text input, falling back to a default for empty/blank/non-finite values
// (Number("") is 0, which would otherwise silently persist 0 when a field is cleared).
// Negatives are clamped to 0 — none of the config numbers are meaningfully negative.
export function parseNum(value: string, fallback: number): number {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, n) : fallback;
}

// Merge an agent's stored (partial) config over the defaults, resolving the per-agent
// system prompt. Nested objects (retrieval/guardrails/spend) are merged one level deep.
export function resolveConfig(agent: Agent): AgentConfig {
  const c = agent.config ?? {};
  return {
    ...DEFAULT_AGENT_CONFIG,
    ...c,
    systemPrompt: c.systemPrompt ?? PROMPTS[agent.id] ?? DEFAULT_PROMPT,
    retrieval: { ...DEFAULT_AGENT_CONFIG.retrieval, ...c.retrieval },
    guardrails: {
      input: c.guardrails?.input ?? DEFAULT_GUARDRAILS.input,
      output: c.guardrails?.output ?? DEFAULT_GUARDRAILS.output,
    },
    spend: { ...DEFAULT_AGENT_CONFIG.spend, ...c.spend },
  };
}

export const AGENTS: Agent[] = [
  {
    id: "triage-copilot",
    name: "SOC Triage Copilot",
    description: "Triages inbound XDR alerts, correlates signals, and drafts an analyst-ready verdict with next steps.",
    status: "published", provider: "Anthropic", model: "claude-opus-5-5",
    owner: "Priya Nair", createdAt: "2026-06-12T10:00:00Z", updatedAt: "2026-10-03T16:20:00Z",
    activeVersion: "v7", versions: 7, tags: ["security", "triage", "rag"],
    calls30d: 48213, cost30d: 1284.6, p95ms: 1840, evalPass: 96.2, drift: "stable",
    callsSpark: [820, 910, 1040, 980, 1120, 1210, 1180, 1340, 1290, 1410, 1380, 1520, 1600, 1680],
    knowledgeBases: ["xdr-playbooks", "mitre-attack"], tools: ["http-enrich-ip", "subagent-log-query"],
    endpoint: "soc-triage-copilot",
  },
  {
    id: "threat-intel-summarizer",
    name: "Threat Intel Summarizer",
    description: "Condenses long threat-intel reports and CVE advisories into structured briefs for the detection team.",
    status: "published", provider: "Anthropic", model: "claude-sonnet-5-5",
    owner: "Diego Fuentes", createdAt: "2026-07-01T09:30:00Z", updatedAt: "2026-10-02T12:10:00Z",
    activeVersion: "v4", versions: 4, tags: ["intel", "summarization", "rag"],
    calls30d: 12940, cost30d: 318.44, p95ms: 2210, evalPass: 93.1, drift: "watch",
    callsSpark: [210, 260, 240, 300, 330, 310, 360, 390, 420, 410, 460, 480, 450, 520],
    knowledgeBases: ["threat-intel-corpus"], tools: ["http-cve-lookup"],
    endpoint: "threat-intel-summarizer",
  },
  {
    id: "incident-report-writer",
    name: "Incident Report Writer",
    description: "Generates post-incident reports from timelines and evidence, following the internal IR template.",
    status: "eval_failing", provider: "OpenAI", model: "gpt-5",
    owner: "Mei-Ling Chen", createdAt: "2026-08-05T15:00:00Z", updatedAt: "2026-10-04T08:05:00Z",
    activeVersion: "v3", versions: 3, tags: ["reporting", "drafting"],
    calls30d: 3820, cost30d: 214.9, p95ms: 3120, evalPass: 78.4, drift: "regressed",
    callsSpark: [180, 170, 160, 150, 140, 120, 130, 110, 100, 95, 88, 70, 64, 52],
    knowledgeBases: ["ir-templates"], tools: [],
    endpoint: "incident-report-writer",
  },
  {
    id: "docs-assistant",
    name: "Product Docs Assistant",
    description: "Answers product and configuration questions grounded in the Trellix documentation set, with citations.",
    status: "published", provider: "Anthropic", model: "claude-haiku-4-5",
    owner: "Sofia Rossi", createdAt: "2026-05-20T11:00:00Z", updatedAt: "2026-10-04T06:45:00Z",
    activeVersion: "v11", versions: 11, tags: ["support", "rag", "q&a"],
    calls30d: 98760, cost30d: 642.18, p95ms: 980, evalPass: 97.8, drift: "stable",
    callsSpark: [2100, 2250, 2400, 2380, 2600, 2700, 2900, 3100, 3050, 3300, 3400, 3520, 3600, 3810],
    knowledgeBases: ["product-docs", "release-notes"], tools: ["http-search-docs"],
    endpoint: "product-docs-assistant",
  },
  {
    id: "log-query-builder",
    name: "Log Query Builder",
    description: "Turns natural-language questions into validated query DSL for the data lake, with a dry-run check.",
    status: "draft", provider: "Anthropic", model: "claude-sonnet-5-5",
    owner: "Diego Fuentes", createdAt: "2026-09-18T13:20:00Z", updatedAt: "2026-10-01T17:55:00Z",
    activeVersion: "v2", versions: 2, tags: ["query", "developer"],
    calls30d: 640, cost30d: 11.2, p95ms: 1520, evalPass: 88.0, drift: "none",
    callsSpark: [12, 18, 22, 20, 28, 31, 40, 38, 44, 52, 48, 60, 66, 71],
    knowledgeBases: ["data-lake-schema"], tools: ["http-dryrun-query", "subagent-log-query"],
  },
  {
    id: "compliance-qa",
    name: "Compliance Q&A",
    description: "Answers SOC 2 and GDPR control questions grounded in the policy library, flagging low-confidence answers.",
    status: "draft", provider: "Google", model: "gemini-2.5-pro",
    owner: "Omar Haddad", createdAt: "2026-09-29T10:10:00Z", updatedAt: "2026-10-03T09:30:00Z",
    activeVersion: "v1", versions: 1, tags: ["compliance", "rag"],
    calls30d: 210, cost30d: 6.4, p95ms: 2640, evalPass: 84.5, drift: "none",
    callsSpark: [4, 6, 9, 7, 11, 14, 12, 18, 16, 22, 19, 24, 28, 30],
    knowledgeBases: ["policy-library"], tools: [],
  },
  {
    id: "onboarding-buddy",
    name: "Engineering Onboarding Buddy",
    description: "Helps new engineers find owners, runbooks and services. Retired in favor of the Docs Assistant.",
    status: "archived", provider: "Mistral", model: "mistral-large-2",
    owner: "Raman Garg", createdAt: "2026-03-11T08:00:00Z", updatedAt: "2026-08-14T10:00:00Z",
    activeVersion: "v5", versions: 5, tags: ["internal"],
    calls30d: 0, cost30d: 0, p95ms: 0, evalPass: 0, drift: "none",
    callsSpark: [40, 36, 30, 22, 18, 12, 8, 6, 4, 2, 1, 0, 0, 0],
    knowledgeBases: [], tools: [],
  },

  /* ---- Trellix People Ops workspace (non-security / HR automation) ---- */
  {
    id: "benefits-qa",
    name: "Benefits Q&A",
    description: "Answers employee benefits, insurance and 401(k) questions grounded in the benefits guide and handbook, with citations.",
    status: "published", provider: "Anthropic", model: "claude-haiku-4-5",
    owner: "Sofia Rossi", createdAt: "2026-06-30T10:00:00Z", updatedAt: "2026-10-04T07:10:00Z",
    activeVersion: "v5", versions: 5, tags: ["hr", "rag", "q&a"],
    calls30d: 15240, cost30d: 92.1, p95ms: 740, evalPass: 95.4, drift: "stable",
    callsSpark: [320, 360, 390, 410, 470, 520, 560, 600, 640, 690, 720, 760, 800, 860],
    knowledgeBases: ["benefits-guide", "employee-handbook"], tools: [],
    endpoint: "benefits-qa",
    workspaceId: "trellix-people",
  },
  {
    id: "pto-helper",
    name: "PTO & Leave Helper",
    description: "Answers time-off and leave-policy questions and looks up an employee's current PTO balance on request.",
    status: "published", provider: "OpenAI", model: "gpt-5-mini",
    owner: "Priya Nair", createdAt: "2026-07-18T09:00:00Z", updatedAt: "2026-10-03T13:40:00Z",
    activeVersion: "v3", versions: 3, tags: ["hr", "self-service"],
    calls30d: 8320, cost30d: 21.4, p95ms: 680, evalPass: 93.0, drift: "stable",
    callsSpark: [180, 210, 230, 260, 280, 300, 330, 360, 380, 410, 430, 460, 480, 520],
    knowledgeBases: ["employee-handbook"], tools: ["http-pto-balance"],
    endpoint: "pto-leave-helper",
    workspaceId: "trellix-people",
  },
  {
    id: "onboarding-concierge",
    name: "Onboarding Concierge",
    description: "Guides new hires through first-week tasks, policies and setup, pulling their start details from the HRIS.",
    status: "published", provider: "Anthropic", model: "claude-haiku-4-5",
    owner: "Diego Fuentes", createdAt: "2026-08-02T11:30:00Z", updatedAt: "2026-10-02T15:20:00Z",
    activeVersion: "v4", versions: 4, tags: ["hr", "onboarding", "rag"],
    calls30d: 4210, cost30d: 28.6, p95ms: 900, evalPass: 94.1, drift: "watch",
    callsSpark: [60, 72, 88, 96, 110, 124, 140, 150, 168, 182, 196, 210, 228, 244],
    knowledgeBases: ["employee-handbook"], tools: ["http-hris-lookup"],
    endpoint: "onboarding-concierge",
    workspaceId: "trellix-people",
  },
  {
    id: "policy-assistant",
    name: "HR Policy Assistant",
    description: "Answers HR policy and code-of-conduct questions grounded in the policy library, flagging low-confidence answers.",
    status: "draft", provider: "Anthropic", model: "claude-sonnet-5-5",
    owner: "Mei-Ling Chen", createdAt: "2026-09-26T10:10:00Z", updatedAt: "2026-10-02T09:30:00Z",
    activeVersion: "v1", versions: 1, tags: ["hr", "compliance", "rag"],
    calls30d: 180, cost30d: 5.2, p95ms: 1600, evalPass: 86.0, drift: "none",
    callsSpark: [3, 5, 8, 6, 10, 12, 11, 16, 14, 19, 17, 22, 25, 28],
    knowledgeBases: ["hr-policy-library"], tools: [],
    workspaceId: "trellix-people",
  },
  {
    id: "resume-screener",
    name: "Resume Screener",
    description: "Screens inbound applications against a role rubric and drafts a shortlist rationale for the recruiter.",
    status: "eval_failing", provider: "Anthropic", model: "claude-sonnet-5-5",
    owner: "Mei-Ling Chen", createdAt: "2026-08-20T14:00:00Z", updatedAt: "2026-10-04T08:30:00Z",
    activeVersion: "v3", versions: 3, tags: ["hr", "screening"],
    calls30d: 2610, cost30d: 48.2, p95ms: 2400, evalPass: 74.2, drift: "regressed",
    callsSpark: [140, 150, 138, 142, 130, 128, 120, 118, 110, 104, 96, 88, 80, 72],
    knowledgeBases: [], tools: ["http-ats-lookup"],
    endpoint: "resume-screener",
    workspaceId: "trellix-people",
    accessPolicy: { visibility: "restricted", useRoles: [], useTeams: ["People"], manageRoles: ["publisher", "admin"], manageTeams: ["People"] },
  },
  {
    id: "survey-summarizer",
    name: "Engagement Survey Summarizer",
    description: "Summarized free-text engagement-survey responses into themes for people managers. Retired after the 2026 survey cycle.",
    status: "archived", provider: "OpenAI", model: "gpt-5-mini",
    owner: "Raman Garg", createdAt: "2026-04-15T08:00:00Z", updatedAt: "2026-09-01T10:00:00Z",
    activeVersion: "v2", versions: 2, tags: ["hr", "internal"],
    calls30d: 0, cost30d: 0, p95ms: 0, evalPass: 0, drift: "none",
    callsSpark: [30, 28, 24, 18, 14, 10, 7, 5, 3, 2, 1, 0, 0, 0],
    knowledgeBases: [], tools: [],
    workspaceId: "trellix-people",
  },
];

export const VERSIONS: Record<string, Version[]> = {
  "triage-copilot": [
    { label: "v7", status: "published", model: "claude-opus-5-5", provider: "Anthropic", createdAt: "2026-10-03T16:20:00Z", createdBy: "Priya Nair", evalVerdict: "pass", note: "Tightened false-positive handling on low-severity DNS alerts." },
    { label: "v6", status: "deprecated", model: "claude-opus-5-5", provider: "Anthropic", createdAt: "2026-09-20T11:00:00Z", createdBy: "Priya Nair", evalVerdict: "pass", note: "Added MITRE ATT&CK knowledge binding." },
    { label: "v5", status: "deprecated", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-09-01T09:00:00Z", createdBy: "Diego Fuentes", evalVerdict: "fail", note: "Sonnet swap regressed verdict quality by 7.4%. Rolled back." },
    { label: "v4", status: "deprecated", model: "claude-opus-5-5", provider: "Anthropic", createdAt: "2026-08-10T14:00:00Z", createdBy: "Priya Nair", evalVerdict: "pass", note: "Raised reasoning effort to high for ambiguous alerts." },
  ],
  "threat-intel-summarizer": [
    { label: "v4", status: "published", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-10-02T12:10:00Z", createdBy: "Diego Fuentes", evalVerdict: "pass", note: "Structured-output schema for the brief header." },
    { label: "v3", status: "deprecated", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-09-12T10:00:00Z", createdBy: "Diego Fuentes", evalVerdict: "low_confidence", note: "Only 8 eval cases, flagged low-confidence at publish." },
  ],
  "incident-report-writer": [
    { label: "v3", status: "published", model: "gpt-5", provider: "OpenAI", createdAt: "2026-10-04T08:05:00Z", createdBy: "Mei-Ling Chen", evalVerdict: "fail", note: "Latest eval failed: completeness dropped on multi-day incidents." },
    { label: "v2", status: "evaluated", model: "gpt-5", provider: "OpenAI", createdAt: "2026-09-15T09:00:00Z", createdBy: "Mei-Ling Chen", evalVerdict: "pass", note: "Stable baseline. Candidate for roll-back." },
  ],
  "docs-assistant": [
    { label: "v11", status: "published", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-10-04T06:45:00Z", createdBy: "Sofia Rossi", evalVerdict: "pass", note: "Re-ingested release-notes KB for 25.4." },
    { label: "v10", status: "deprecated", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-09-22T06:45:00Z", createdBy: "Sofia Rossi", evalVerdict: "pass", note: "Hybrid retrieval (BM25 + vector) enabled." },
  ],
  "log-query-builder": [
    { label: "v2", status: "evaluated", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-10-01T17:55:00Z", createdBy: "Diego Fuentes", evalVerdict: "pass", note: "Added dry-run validation tool before returning a query." },
    { label: "v1", status: "draft", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-09-18T13:20:00Z", createdBy: "Diego Fuentes", evalVerdict: "none", note: "Initial draft." },
  ],
  "compliance-qa": [
    { label: "v1", status: "draft", model: "gemini-2.5-pro", provider: "Google", createdAt: "2026-09-29T10:10:00Z", createdBy: "Omar Haddad", evalVerdict: "none", note: "Initial draft, eval set in progress." },
  ],
  "onboarding-buddy": [
    { label: "v5", status: "deprecated", model: "mistral-large-2", provider: "Mistral", createdAt: "2026-08-14T10:00:00Z", createdBy: "Raman Garg", evalVerdict: "pass", note: "Archived. Superseded by Product Docs Assistant." },
  ],
  "benefits-qa": [
    { label: "v5", status: "published", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-10-04T07:10:00Z", createdBy: "Sofia Rossi", evalVerdict: "pass", note: "Re-ingested the 2026 benefits guide." },
    { label: "v4", status: "deprecated", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-09-10T09:00:00Z", createdBy: "Sofia Rossi", evalVerdict: "pass", note: "Added citations to the handbook source." },
    { label: "v3", status: "deprecated", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-08-18T09:00:00Z", createdBy: "Sofia Rossi", evalVerdict: "pass", note: "Tightened refusal on personal medical advice." },
  ],
  "pto-helper": [
    { label: "v3", status: "published", model: "gpt-5-mini", provider: "OpenAI", createdAt: "2026-10-03T13:40:00Z", createdBy: "Priya Nair", evalVerdict: "pass", note: "Added the PTO-balance lookup tool." },
    { label: "v2", status: "deprecated", model: "gpt-5-mini", provider: "OpenAI", createdAt: "2026-08-22T10:00:00Z", createdBy: "Priya Nair", evalVerdict: "pass", note: "Leave-policy coverage for regional holidays." },
  ],
  "onboarding-concierge": [
    { label: "v4", status: "published", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-10-02T15:20:00Z", createdBy: "Diego Fuentes", evalVerdict: "pass", note: "HRIS lookup for personalized first-week tasks." },
    { label: "v3", status: "deprecated", model: "claude-haiku-4-5", provider: "Anthropic", createdAt: "2026-09-05T11:00:00Z", createdBy: "Diego Fuentes", evalVerdict: "pass", note: "Handbook re-ingest for the new PTO policy." },
  ],
  "policy-assistant": [
    { label: "v1", status: "draft", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-09-26T10:10:00Z", createdBy: "Mei-Ling Chen", evalVerdict: "none", note: "Initial draft, eval set in progress." },
  ],
  "resume-screener": [
    { label: "v3", status: "published", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-10-04T08:30:00Z", createdBy: "Mei-Ling Chen", evalVerdict: "fail", note: "Latest eval failed: shortlist rationale regressed on senior roles." },
    { label: "v2", status: "evaluated", model: "claude-sonnet-5-5", provider: "Anthropic", createdAt: "2026-09-12T10:00:00Z", createdBy: "Mei-Ling Chen", evalVerdict: "pass", note: "Stable baseline. Candidate for roll-back." },
  ],
  "survey-summarizer": [
    { label: "v2", status: "deprecated", model: "gpt-5-mini", provider: "OpenAI", createdAt: "2026-09-01T10:00:00Z", createdBy: "Raman Garg", evalVerdict: "pass", note: "Archived after the 2026 survey cycle." },
  ],
};

/* ---------------------------------------------------------------- Knowledge */
/* @backend A credential is never sent back in full. The server returns only a masked display value
   and an opaque vault path; the raw secret is write-only (POSTed once to the vault on capture).
   Mirrors ProviderKey.masked. [ref: backend/01-data-model.md §4.6, backend-integration-plan.md Part 1 step 11] */
export type CredentialRef = { masked: string; vaultPath: string };

/* @backend Shape of data_sources.config (jsonb). `auth` is intentionally omitted — it is derivable
   from `connector` via CONNECTOR_DEFS, so storing it would invite drift. */
export type DataSourceConfig = {
  connector?: Connector;
  fields?: Record<string, string>; // non-secret config values (bucket, space key, repo url, …)
  credentialRef?: CredentialRef; // masked only; raw credential lives in the vault
};

export type DataSource = {
  name: string; type: "File" | "Web" | "Connector" | "Manual"; status: "synced" | "syncing" | "error"; docs: number; updatedAt: string;
  config?: DataSourceConfig;
};
export type KnowledgeBase = {
  id: string; name: string; embeddingModel: string; docs: number; chunks: number;
  sizeMB: number; lastSync: string; status: "ready" | "indexing" | "error"; boundAgents: number;
  sources: DataSource[];
  workspaceId?: string;
  accessPolicy?: AccessPolicy;
};

export const KNOWLEDGE_BASES: KnowledgeBase[] = [
  {
    id: "product-docs", name: "Product Documentation", embeddingModel: "text-embedding-3-large",
    docs: 2140, chunks: 41820, sizeMB: 186.4, lastSync: "2026-10-04T05:00:00Z", status: "ready", boundAgents: 1,
    sources: [
      { name: "docs.trellix.com crawl", type: "Web", status: "synced", docs: 1860, updatedAt: "2026-10-04T05:00:00Z" },
      { name: "admin-guide.pdf", type: "File", status: "synced", docs: 1, updatedAt: "2026-09-30T10:00:00Z" },
      { name: "Confluence: Platform space", type: "Connector", status: "syncing", docs: 279, updatedAt: "2026-10-04T05:02:00Z" },
    ],
  },
  {
    id: "xdr-playbooks", name: "XDR Response Playbooks", embeddingModel: "text-embedding-3-large",
    docs: 342, chunks: 6120, sizeMB: 24.1, lastSync: "2026-10-03T22:00:00Z", status: "ready", boundAgents: 1,
    sources: [
      { name: "playbooks/ (Git repo)", type: "Connector", status: "synced", docs: 320, updatedAt: "2026-10-03T22:00:00Z" },
      { name: "escalation-matrix.xlsx", type: "File", status: "synced", docs: 22, updatedAt: "2026-09-28T09:00:00Z" },
    ],
  },
  {
    id: "mitre-attack", name: "MITRE ATT&CK", embeddingModel: "text-embedding-3-large",
    docs: 790, chunks: 9240, sizeMB: 38.9, lastSync: "2026-10-01T04:00:00Z", status: "ready", boundAgents: 1,
    sources: [{ name: "attack.mitre.org crawl", type: "Web", status: "synced", docs: 790, updatedAt: "2026-10-01T04:00:00Z" }],
  },
  {
    id: "threat-intel-corpus", name: "Threat Intel Corpus", embeddingModel: "voyage-3-large",
    docs: 5820, chunks: 132400, sizeMB: 612.0, lastSync: "2026-10-04T03:30:00Z", status: "indexing", boundAgents: 1,
    sources: [
      { name: "S3: intel-reports/", type: "Connector", status: "syncing", docs: 5600, updatedAt: "2026-10-04T03:30:00Z" },
      { name: "CVE advisories feed", type: "Web", status: "synced", docs: 220, updatedAt: "2026-10-04T02:00:00Z" },
    ],
  },
  {
    id: "ir-templates", name: "IR Templates", embeddingModel: "text-embedding-3-small",
    docs: 48, chunks: 610, sizeMB: 2.2, lastSync: "2026-09-25T10:00:00Z", status: "error", boundAgents: 1,
    sources: [{ name: "ir-templates.docx", type: "File", status: "error", docs: 48, updatedAt: "2026-09-25T10:00:00Z" }],
  },
  {
    id: "policy-library", name: "Policy Library (SOC 2 / GDPR)", embeddingModel: "text-embedding-3-large",
    docs: 410, chunks: 8900, sizeMB: 31.5, lastSync: "2026-10-02T06:00:00Z", status: "ready", boundAgents: 1,
    sources: [{ name: "GRC: policy-library", type: "Connector", status: "synced", docs: 410, updatedAt: "2026-10-02T06:00:00Z" }],
  },

  /* ---- Trellix People Ops workspace ---- */
  {
    id: "employee-handbook", name: "Employee Handbook", embeddingModel: "text-embedding-3-small",
    docs: 318, chunks: 5240, sizeMB: 18.4, lastSync: "2026-10-04T05:30:00Z", status: "ready", boundAgents: 3,
    sources: [
      { name: "employee-handbook-2026.pdf", type: "File", status: "synced", docs: 1, updatedAt: "2026-10-01T09:00:00Z" },
      { name: "Confluence: People space", type: "Connector", status: "synced", docs: 317, updatedAt: "2026-10-04T05:30:00Z" },
    ],
    workspaceId: "trellix-people",
  },
  {
    id: "benefits-guide", name: "Benefits Guide", embeddingModel: "text-embedding-3-large",
    docs: 142, chunks: 2410, sizeMB: 9.1, lastSync: "2026-10-03T06:00:00Z", status: "ready", boundAgents: 1,
    sources: [
      { name: "benefits-2026.pdf", type: "File", status: "synced", docs: 1, updatedAt: "2026-09-29T10:00:00Z" },
      { name: "benefits portal crawl", type: "Web", status: "synced", docs: 141, updatedAt: "2026-10-03T06:00:00Z" },
    ],
    workspaceId: "trellix-people",
  },
  {
    id: "hr-policy-library", name: "HR Policy Library", embeddingModel: "text-embedding-3-small",
    docs: 264, chunks: 4120, sizeMB: 14.7, lastSync: "2026-10-04T04:00:00Z", status: "indexing", boundAgents: 1,
    sources: [{ name: "SharePoint: HR policies", type: "Connector", status: "syncing", docs: 264, updatedAt: "2026-10-04T04:00:00Z" }],
    workspaceId: "trellix-people",
  },
];

export type IngestionJob = { id: string; source: string; kb: string; status: "completed" | "running" | "failed" | "queued"; docs: number; startedAt: string; duration: string };
export const INGESTION_JOBS: IngestionJob[] = [
  { id: "job-9f20", source: "Confluence: Platform space", kb: "Product Documentation", status: "running", docs: 279, startedAt: "2026-10-04T05:02:00Z", duration: "4m 12s" },
  { id: "job-9f1a", source: "S3: intel-reports/", kb: "Threat Intel Corpus", status: "running", docs: 5600, startedAt: "2026-10-04T03:30:00Z", duration: "1h 38m" },
  { id: "job-9e80", source: "docs.trellix.com crawl", kb: "Product Documentation", status: "completed", docs: 1860, startedAt: "2026-10-04T05:00:00Z", duration: "11m 40s" },
  { id: "job-9e42", source: "ir-templates.docx", kb: "IR Templates", status: "failed", docs: 0, startedAt: "2026-09-25T10:00:00Z", duration: "0m 8s" },
  { id: "job-9d11", source: "playbooks/ (Git repo)", kb: "XDR Response Playbooks", status: "completed", docs: 320, startedAt: "2026-10-03T22:00:00Z", duration: "6m 02s" },
];

/* ---------------------------------------------------------------- Tools/MCP */
export type McpTool = { name: string; description: string; enabled: boolean };
export type McpServer = {
  id: string; name: string; transport: "stdio" | "http"; endpoint: string;
  status: "connected" | "error" | "disconnected"; auth: string; toolCount: number;
  lastDiscovery: string; boundAgents: number; tools: McpTool[];
  workspaceId?: string;
  accessPolicy?: AccessPolicy;
};

export const MCP_SERVERS: McpServer[] = [
  {
    id: "jira-mcp", name: "Jira", transport: "http", endpoint: "https://mcp.internal/jira",
    status: "connected", auth: "OAuth 2.0", toolCount: 6, lastDiscovery: "2026-10-04T04:10:00Z", boundAgents: 2,
    tools: [
      { name: "search_issues", description: "Search Jira issues with JQL", enabled: true },
      { name: "get_issue", description: "Fetch a single issue by key", enabled: true },
      { name: "create_issue", description: "Create a new issue", enabled: false },
      { name: "add_comment", description: "Comment on an issue", enabled: false },
    ],
  },
  {
    id: "vt-mcp", name: "VirusTotal", transport: "http", endpoint: "https://mcp.internal/virustotal",
    status: "connected", auth: "Bearer token", toolCount: 4, lastDiscovery: "2026-10-04T04:10:00Z", boundAgents: 1,
    tools: [
      { name: "lookup_hash", description: "Reputation for a file hash", enabled: true },
      { name: "lookup_ip", description: "Reputation for an IP address", enabled: true },
      { name: "lookup_domain", description: "Reputation for a domain", enabled: true },
    ],
  },
  {
    id: "splunk-mcp", name: "Splunk", transport: "stdio", endpoint: "splunk-mcp --readonly",
    status: "connected", auth: "Service account", toolCount: 3, lastDiscovery: "2026-10-03T20:00:00Z", boundAgents: 1,
    tools: [
      { name: "run_search", description: "Run a read-only SPL search", enabled: true },
      { name: "list_indexes", description: "List available indexes", enabled: true },
    ],
  },
  {
    id: "gdrive-mcp", name: "Google Drive", transport: "http", endpoint: "https://mcp.internal/gdrive",
    status: "error", auth: "OAuth 2.0", toolCount: 0, lastDiscovery: "2026-10-04T04:10:00Z", boundAgents: 0,
    tools: [],
  },

  /* ---- Trellix People Ops workspace ---- */
  {
    id: "workday-mcp", name: "Workday", transport: "http", endpoint: "https://mcp.internal/workday",
    status: "connected", auth: "OAuth 2.0", toolCount: 3, lastDiscovery: "2026-10-04T04:15:00Z", boundAgents: 2,
    tools: [
      { name: "get_worker", description: "Fetch an employee record by id", enabled: true },
      { name: "list_time_off", description: "List time-off balances and requests", enabled: true },
      { name: "submit_time_off", description: "Submit a time-off request", enabled: false },
    ],
    workspaceId: "trellix-people",
  },
  {
    id: "greenhouse-mcp", name: "Greenhouse", transport: "http", endpoint: "https://mcp.internal/greenhouse",
    status: "connected", auth: "Bearer token", toolCount: 2, lastDiscovery: "2026-10-04T04:15:00Z", boundAgents: 1,
    tools: [
      { name: "search_candidates", description: "Search candidates and applications", enabled: true },
      { name: "get_application", description: "Fetch a single application by id", enabled: true },
    ],
    workspaceId: "trellix-people",
  },
  {
    id: "slack-hr-mcp", name: "Slack (People)", transport: "http", endpoint: "https://mcp.internal/slack-people",
    status: "error", auth: "OAuth 2.0", toolCount: 0, lastDiscovery: "2026-10-04T04:15:00Z", boundAgents: 0,
    tools: [],
    workspaceId: "trellix-people",
  },
];

/* @backend Shape of tools.config/schema (jsonb) for an HTTP tool. The secret behind `auth` is vaulted;
   only `authRef` (masked) is persisted/returned, like CredentialRef. [ref: backend/01-data-model.md §4.6] */
export type HttpToolConfig = {
  method: "GET" | "POST" | "PUT" | "DELETE";
  url: string;
  auth: "none" | "bearer" | "api_key_header" | "basic";
  authRef?: CredentialRef; // present when auth !== "none"
  headers?: string; // raw "Key: Value" per line
  argsSchema?: string; // JSON text, stored as-is (not parsed here)
};
export type RepoTool = { name: string; kind: "HTTP" | "Built-in" | "Sub-agent"; description: string; usedBy: number; workspaceId?: string; accessPolicy?: AccessPolicy; httpConfig?: HttpToolConfig };
export const TOOLS_REPO: RepoTool[] = [
  { name: "http-enrich-ip", kind: "HTTP", description: "Enrich an IP with geo, ASN and reputation", usedBy: 2 },
  { name: "http-cve-lookup", kind: "HTTP", description: "Look up CVE details by identifier", usedBy: 1 },
  { name: "http-search-docs", kind: "HTTP", description: "Full-text search over the docs index", usedBy: 1 },
  { name: "http-dryrun-query", kind: "HTTP", description: "Validate a query against the data lake without running it", usedBy: 1 },
  { name: "subagent-log-query", kind: "Sub-agent", description: "Delegate to the Log Query Builder agent", usedBy: 2 },
  { name: "code-exec-python", kind: "Built-in", description: "Sandboxed Python for calculations and parsing", usedBy: 3 },
  /* ---- Trellix People Ops workspace ---- */
  { name: "http-hris-lookup", kind: "HTTP", description: "Look up an employee record in the HRIS (Workday)", usedBy: 1, workspaceId: "trellix-people" },
  { name: "http-pto-balance", kind: "HTTP", description: "Fetch an employee's PTO and leave balance", usedBy: 1, workspaceId: "trellix-people" },
  { name: "http-ats-lookup", kind: "HTTP", description: "Fetch candidate records from the ATS (Greenhouse)", usedBy: 1, workspaceId: "trellix-people" },
];

/* ------------------------------------------------------------------- Evals */
export type EvalDimension = { name: string; score: number; baseline: number };
export type EvalCase = { input: string; issue: string };
export type EvalRun = {
  id: string; agentId: string; agent: string; version: string; baseline: string;
  quality: number; qualityDelta: number; costDelta: number; latencyDelta: number;
  cases: number; verdict: Verdict; date: string; trigger: string;
  workspaceId?: string;
  // Why the run landed where it did, and (for failures) the drill-down detail.
  reason?: string;
  dimensions?: EvalDimension[];
  failingCases?: EvalCase[];
};
export const EVAL_RUNS: EvalRun[] = [
  { id: "ev-4821", agentId: "triage-copilot", agent: "SOC Triage Copilot", version: "v7", baseline: "v6", quality: 96.2, qualityDelta: 1.4, costDelta: -3.1, latencyDelta: -40, cases: 64, verdict: "pass", date: "2026-10-03T16:18:00Z", trigger: "Prompt edit", reason: "All dimensions at or above baseline; the prompt edit improved quality with no regressions." },
  {
    id: "ev-4805", agentId: "incident-report-writer", agent: "Incident Report Writer", version: "v3", baseline: "v2", quality: 78.4, qualityDelta: -9.2, costDelta: 12.0, latencyDelta: 320, cases: 30, verdict: "fail", date: "2026-10-04T08:02:00Z", trigger: "Model swap (gpt-5)",
    reason: "Completeness dropped on multi-day incidents. After the swap to gpt-5 the model omitted timeline steps and evidence links on longer reports, so the verdict fell below the 90% publish bar.",
    dimensions: [
      { name: "Accuracy", score: 88, baseline: 90 },
      { name: "Completeness", score: 64, baseline: 86 },
      { name: "Faithfulness", score: 79, baseline: 88 },
      { name: "Safety", score: 96, baseline: 97 },
    ],
    failingCases: [
      { input: "5-day ransomware incident with 3 lateral-movement pivots", issue: "Omitted days 3 to 4 from the timeline and dropped 4 of 9 evidence links." },
      { input: "Multi-host compromise across 2 subnets", issue: "Merged two hosts into one entry, understating the blast radius." },
    ],
  },
  // The last passing version for incident-report-writer — the baseline v3 regressed against (so rollback restores a real score).
  { id: "ev-4788", agentId: "incident-report-writer", agent: "Incident Report Writer", version: "v2", baseline: "v1", quality: 87.6, qualityDelta: 3.1, costDelta: -0.8, latencyDelta: -30, cases: 30, verdict: "pass", date: "2026-09-30T10:12:00Z", trigger: "Prompt edit", reason: "All dimensions at or above baseline; the last passing version before the gpt-5 model swap regressed completeness." },
  { id: "ev-4790", agentId: "docs-assistant", agent: "Product Docs Assistant", version: "v11", baseline: "v10", quality: 97.8, qualityDelta: 0.6, costDelta: 0.2, latencyDelta: -12, cases: 120, verdict: "pass", date: "2026-10-04T06:40:00Z", trigger: "Knowledge re-ingest", reason: "All dimensions at or above baseline after the re-ingest; citations stayed accurate." },
  { id: "ev-4772", agentId: "threat-intel-summarizer", agent: "Threat Intel Summarizer", version: "v4", baseline: "v3", quality: 93.1, qualityDelta: 2.0, costDelta: -1.0, latencyDelta: 60, cases: 42, verdict: "pass", date: "2026-10-02T12:06:00Z", trigger: "Schema change", reason: "All dimensions at or above baseline; the structured-output schema improved consistency." },
  { id: "ev-4760", agentId: "log-query-builder", agent: "Log Query Builder", version: "v2", baseline: "v1", quality: 88.0, qualityDelta: 5.2, costDelta: 2.4, latencyDelta: 110, cases: 18, verdict: "low_confidence", date: "2026-10-01T17:50:00Z", trigger: "Tool added", reason: "Quality improved but the suite has only 18 cases, below the threshold for a confident verdict. Add cases before relying on this result." },
  { id: "ev-4741", agentId: "compliance-qa", agent: "Compliance Q&A", version: "v1", baseline: "none", quality: 84.5, qualityDelta: 0, costDelta: 0, latencyDelta: 0, cases: 12, verdict: "low_confidence", date: "2026-10-03T09:25:00Z", trigger: "First baseline", reason: "First baseline with only 12 cases and no prior version to compare against. Expand the suite to trust the score." },
  /* ---- Trellix People Ops workspace (workspaceId inherited from the agent) ---- */
  { id: "ev-5010", agentId: "benefits-qa", agent: "Benefits Q&A", version: "v5", baseline: "v4", quality: 95.4, qualityDelta: 0.9, costDelta: -0.4, latencyDelta: -20, cases: 58, verdict: "pass", date: "2026-10-04T07:08:00Z", trigger: "Knowledge re-ingest", reason: "All dimensions at or above baseline; the refreshed benefits guide kept answers grounded." },
  {
    id: "ev-5004", agentId: "resume-screener", agent: "Resume Screener", version: "v3", baseline: "v2", quality: 74.2, qualityDelta: -11.3, costDelta: 8.0, latencyDelta: 260, cases: 40, verdict: "fail", date: "2026-10-04T08:28:00Z", trigger: "Prompt edit",
    reason: "Shortlist rationale regressed on senior roles. The prompt edit made justifications stop citing the role rubric, and in two cases the model leaned on proxy signals for seniority.",
    dimensions: [
      { name: "Rubric adherence", score: 70, baseline: 88 },
      { name: "Neutrality", score: 95, baseline: 96 },
      { name: "Completeness", score: 78, baseline: 85 },
      { name: "Faithfulness", score: 74, baseline: 90 },
    ],
    failingCases: [
      { input: "Staff Engineer, 12 years experience", issue: "Recommended reject without citing any rubric criterion." },
      { input: "Senior PM, career switcher", issue: "Inferred seniority from graduation year, a proxy signal the rubric forbids." },
    ],
  },
  // The last passing version for resume-screener — the baseline v3 regressed against (so rollback restores a real score).
  { id: "ev-4996", agentId: "resume-screener", agent: "Resume Screener", version: "v2", baseline: "v1", quality: 85.5, qualityDelta: 2.4, costDelta: -0.5, latencyDelta: -15, cases: 40, verdict: "pass", date: "2026-09-29T11:40:00Z", trigger: "Rubric update", reason: "All dimensions at or above baseline; the last passing version before the prompt edit regressed rubric adherence." },
  { id: "ev-4998", agentId: "pto-helper", agent: "PTO & Leave Helper", version: "v3", baseline: "v2", quality: 93.0, qualityDelta: 2.6, costDelta: 1.1, latencyDelta: 40, cases: 32, verdict: "pass", date: "2026-10-03T13:36:00Z", trigger: "Tool added", reason: "All dimensions at or above baseline; the PTO-balance tool improved task completion." },
  { id: "ev-4990", agentId: "onboarding-concierge", agent: "Onboarding Concierge", version: "v4", baseline: "v3", quality: 94.1, qualityDelta: 1.2, costDelta: -0.2, latencyDelta: 15, cases: 36, verdict: "pass", date: "2026-10-02T15:16:00Z", trigger: "Knowledge re-ingest", reason: "All dimensions at or above baseline; HRIS lookups stayed accurate after the re-ingest." },
  { id: "ev-4982", agentId: "policy-assistant", agent: "HR Policy Assistant", version: "v1", baseline: "none", quality: 86.0, qualityDelta: 0, costDelta: 0, latencyDelta: 0, cases: 9, verdict: "low_confidence", date: "2026-10-02T09:26:00Z", trigger: "First baseline", reason: "First baseline with only 9 cases and no prior version to compare against. Expand the suite to trust the score." },
];

/* -------------------------------------------------------------------- Runs */
export type Run = {
  id: string; agentId: string; agent: string; version: string;
  source: "Playground" | "API" | "Eval" | "Channel"; status: "success" | "error" | "blocked";
  latencyMs: number; tokens: number; cost: number; time: string; guardrail: string;
  workspaceId?: string;
  caller?: string;
  subjectType?: "user" | "service";
};
const AGENT_BY = (id: string) => AGENTS.find((a) => a.id === id)!.name;
export const RUNS: Run[] = [
  { id: "run-7f3a21", agentId: "docs-assistant", agent: AGENT_BY("docs-assistant"), version: "v11", source: "API", status: "success", latencyMs: 910, tokens: 3420, cost: 0.0021, time: "2026-10-04T09:14:00Z", guardrail: "passed" },
  { id: "run-7f3a1e", agentId: "triage-copilot", agent: AGENT_BY("triage-copilot"), version: "v7", source: "API", status: "success", latencyMs: 1780, tokens: 9120, cost: 0.034, time: "2026-10-04T09:13:30Z", guardrail: "passed" },
  { id: "run-7f3a15", agentId: "triage-copilot", agent: AGENT_BY("triage-copilot"), version: "v7", source: "API", status: "blocked", latencyMs: 210, tokens: 420, cost: 0.001, time: "2026-10-04T09:12:10Z", guardrail: "PII blocked on input" },
  { id: "run-7f39e0", agentId: "incident-report-writer", agent: AGENT_BY("incident-report-writer"), version: "v3", source: "Playground", status: "error", latencyMs: 4120, tokens: 6600, cost: 0.052, time: "2026-10-04T09:05:00Z", guardrail: "n/a (provider timeout)" },
  { id: "run-7f39c2", agentId: "docs-assistant", agent: AGENT_BY("docs-assistant"), version: "v11", source: "API", status: "success", latencyMs: 860, tokens: 2980, cost: 0.0018, time: "2026-10-04T09:02:40Z", guardrail: "passed" },
  { id: "run-7f3980", agentId: "threat-intel-summarizer", agent: AGENT_BY("threat-intel-summarizer"), version: "v4", source: "API", status: "success", latencyMs: 2060, tokens: 14200, cost: 0.041, time: "2026-10-04T08:58:00Z", guardrail: "passed" },
  { id: "run-7f3955", agentId: "docs-assistant", agent: AGENT_BY("docs-assistant"), version: "v11", source: "Eval", status: "success", latencyMs: 940, tokens: 3100, cost: 0.0019, time: "2026-10-04T06:40:00Z", guardrail: "passed" },
  { id: "run-7f3930", agentId: "triage-copilot", agent: AGENT_BY("triage-copilot"), version: "v7", source: "API", status: "success", latencyMs: 1920, tokens: 10400, cost: 0.038, time: "2026-10-04T08:40:12Z", guardrail: "passed" },
  { id: "run-7f3901", agentId: "docs-assistant", agent: AGENT_BY("docs-assistant"), version: "v11", source: "API", status: "blocked", latencyMs: 180, tokens: 300, cost: 0.0004, time: "2026-10-04T08:31:00Z", guardrail: "Off-topic blocked" },
  { id: "run-7f38d4", agentId: "log-query-builder", agent: AGENT_BY("log-query-builder"), version: "v2", source: "Playground", status: "success", latencyMs: 1490, tokens: 2200, cost: 0.006, time: "2026-10-04T08:20:00Z", guardrail: "passed" },
  /* ---- Trellix People Ops workspace (workspaceId inherited from the agent) ---- */
  { id: "run-8a1200", agentId: "benefits-qa", agent: AGENT_BY("benefits-qa"), version: "v5", source: "API", status: "success", latencyMs: 720, tokens: 2600, cost: 0.0014, time: "2026-10-04T09:12:00Z", guardrail: "passed" },
  { id: "run-8a11e2", agentId: "benefits-qa", agent: AGENT_BY("benefits-qa"), version: "v5", source: "API", status: "blocked", latencyMs: 190, tokens: 360, cost: 0.0003, time: "2026-10-04T09:08:30Z", guardrail: "PII blocked on input" },
  { id: "run-8a1190", agentId: "pto-helper", agent: AGENT_BY("pto-helper"), version: "v3", source: "API", status: "success", latencyMs: 680, tokens: 1800, cost: 0.0009, time: "2026-10-04T09:04:10Z", guardrail: "passed" },
  { id: "run-8a1155", agentId: "onboarding-concierge", agent: AGENT_BY("onboarding-concierge"), version: "v4", source: "Playground", status: "success", latencyMs: 880, tokens: 2400, cost: 0.0022, time: "2026-10-04T08:52:00Z", guardrail: "passed" },
  { id: "run-8a1120", agentId: "resume-screener", agent: AGENT_BY("resume-screener"), version: "v3", source: "API", status: "error", latencyMs: 3600, tokens: 5200, cost: 0.039, time: "2026-10-04T08:46:00Z", guardrail: "n/a (provider timeout)" },
  { id: "run-8a10f4", agentId: "benefits-qa", agent: AGENT_BY("benefits-qa"), version: "v5", source: "API", status: "success", latencyMs: 700, tokens: 2500, cost: 0.0013, time: "2026-10-04T08:40:00Z", guardrail: "passed" },
  { id: "run-8a10c0", agentId: "pto-helper", agent: AGENT_BY("pto-helper"), version: "v3", source: "API", status: "success", latencyMs: 650, tokens: 1700, cost: 0.0008, time: "2026-10-04T08:31:00Z", guardrail: "passed" },
  /* ---- Channel-sourced runs (federated Okta user via a messaging app) ---- */
  { id: "run-7f3b40", agentId: "triage-copilot", agent: AGENT_BY("triage-copilot"), version: "v7", source: "Channel", status: "success", latencyMs: 1960, tokens: 8800, cost: 0.031, time: "2026-10-04T09:16:00Z", guardrail: "passed", caller: "slack · @priya.nair", subjectType: "user" },
  { id: "run-8a1240", agentId: "benefits-qa", agent: AGENT_BY("benefits-qa"), version: "v5", source: "Channel", status: "success", latencyMs: 740, tokens: 2700, cost: 0.0015, time: "2026-10-04T09:15:20Z", guardrail: "passed", caller: "gchat · @diego.fuentes", subjectType: "user" },
];

/* ------------------------------------------------------ Tokens & keys */
export type ApiToken = { id: string; name: string; agent: string; prefix: string; created: string; lastUsed: string; scopes: string };
export const API_TOKENS: ApiToken[] = [
  { id: "tok-1", name: "soc-dashboard-prod", agent: "SOC Triage Copilot", prefix: "ac_live_7Q2…", created: "2026-09-10T10:00:00Z", lastUsed: "2026-10-04T09:13:00Z", scopes: "invoke" },
  { id: "tok-2", name: "support-widget", agent: "Product Docs Assistant", prefix: "ac_live_Kp9…", created: "2026-08-02T14:00:00Z", lastUsed: "2026-10-04T09:14:00Z", scopes: "invoke" },
  { id: "tok-3", name: "intel-cron", agent: "Threat Intel Summarizer", prefix: "ac_live_Zx4…", created: "2026-09-25T09:00:00Z", lastUsed: "2026-10-04T08:58:00Z", scopes: "invoke" },
];

export type ProviderKey = { provider: string; label: string; masked: string; added: string; status: "active" | "expiring" };
export const PROVIDER_KEYS: ProviderKey[] = [
  { provider: "Anthropic", label: "anthropic-prod", masked: "sk-ant-••••••••••••4f2a", added: "2026-05-01T10:00:00Z", status: "active" },
  { provider: "OpenAI", label: "openai-prod", masked: "sk-••••••••••••9c71", added: "2026-05-01T10:05:00Z", status: "active" },
  { provider: "Google", label: "vertex-sa", masked: "svc-••••••••••••d30b", added: "2026-07-14T11:00:00Z", status: "expiring" },
  { provider: "Mistral", label: "mistral-dev", masked: "••••••••••••a118", added: "2026-06-20T09:00:00Z", status: "active" },
];

/* ---------------------------------------------------------------- Channels */
/* @backend A messaging-app channel: one platform install bound to ONE workspace, routing to MANY
   agents. End users are federated internal Okta identities (channel okta_group ∩ agent accessPolicy);
   a federated human is subjectType "user", a service account "service" — no new subject class.
   Credentials are MULTI-SECRET (bot token + signing secret), each a masked CredentialRef; the raw
   secret is write-only to the vault. [ref: channels-design.md, backend/12-channels.md] */
export type Channel = {
  id: string;
  name: string;
  platform: ChannelPlatform;
  status: "connected" | "error" | "disconnected" | "setup_pending";
  agentIds: string[]; // agents reachable through this channel (one workspace -> many agents)
  defaultAgentId?: string;
  routing: "slash" | "picker" | "default"; // how a message selects an agent
  identityMode: "federated" | "service";
  oktaGroup?: string; // Okta group granted this channel's workspace
  credentials: Record<string, CredentialRef>; // keyed by credentialField key; masked only
  webhookUrl?: string; // the Catalyst ingress URL registered in the platform
  installedBy: string;
  createdAt: string;
  updatedAt: string;
  lastEventAt?: string;
  workspaceId?: string;
  accessPolicy?: AccessPolicy;
};

export const CHANNELS_SEED: Channel[] = [
  {
    id: "slack-soc", name: "SOC Slack", platform: "slack", status: "connected",
    agentIds: ["triage-copilot", "threat-intel-summarizer"], defaultAgentId: "triage-copilot",
    routing: "slash", identityMode: "federated", oktaGroup: "okta-soc",
    credentials: {
      botToken: { masked: "xoxb-7••••••••a4f2", vaultPath: "vault://channel/slack-soc-bot" },
      signingSecret: { masked: "9f1a••••••••c7d0", vaultPath: "vault://channel/slack-soc-sign" },
    },
    webhookUrl: "https://catalyst.internal/channels/slack/events/slack-soc",
    installedBy: "Priya Nair", createdAt: "2026-09-18T10:00:00Z", updatedAt: "2026-10-03T12:00:00Z",
    lastEventAt: "2026-10-04T09:16:00Z",
  },
  {
    id: "gchat-people", name: "People Ops Google Chat", platform: "gchat", status: "connected",
    agentIds: ["benefits-qa", "pto-helper"], defaultAgentId: "benefits-qa",
    routing: "default", identityMode: "federated", oktaGroup: "okta-people",
    credentials: {
      serviceAccountKey: { masked: "svc-••••••••d30b", vaultPath: "vault://channel/gchat-people-sa" },
    },
    webhookUrl: "https://catalyst.internal/channels/gchat/events/gchat-people",
    installedBy: "Diego Fuentes", createdAt: "2026-09-22T09:00:00Z", updatedAt: "2026-10-02T15:30:00Z",
    lastEventAt: "2026-10-04T09:15:20Z", workspaceId: "trellix-people",
  },
  {
    id: "teams-grc", name: "Compliance Teams", platform: "teams", status: "setup_pending",
    agentIds: [], routing: "default", identityMode: "federated", oktaGroup: "okta-grc",
    credentials: {
      clientSecret: { masked: "", vaultPath: "" },
    },
    installedBy: "Sofia Rossi", createdAt: "2026-10-04T08:00:00Z", updatedAt: "2026-10-04T08:00:00Z",
    workspaceId: "trellix-grc",
  },
];

/* -------------------------------------------------------- Dashboard series */
/* Chart series and headline stat tiles are keyed by workspace so each tenant
   shows its own data. Selectors fall back to the trellix-xdr values for any
   workspace without an explicit entry (e.g. grc/labs), preserving today's view. */
export type Series = { name: string; values: number[] };

export const SPEND_LABELS = ["Sep 5", "Sep 9", "Sep 13", "Sep 17", "Sep 21", "Sep 25", "Sep 29", "Oct 3"];
const SPEND_SERIES_BY_WS: Record<string, Series[]> = {
  "trellix-xdr": [
    { name: "Anthropic", values: [58, 64, 71, 69, 82, 91, 104, 118] },
    { name: "OpenAI", values: [22, 20, 26, 31, 28, 34, 30, 38] },
    { name: "Google", values: [4, 5, 6, 6, 8, 9, 11, 12] },
  ],
  "trellix-people": [
    { name: "Anthropic", values: [6, 7, 8, 9, 10, 11, 12, 13] },
    { name: "OpenAI", values: [2, 2, 3, 3, 4, 4, 5, 5] },
  ],
  "trellix-grc": [{ name: "Google", values: [1, 1, 2, 2, 3, 3, 4, 4] }],
  "trellix-labs": [{ name: "Mistral", values: [3, 2, 1, 1, 0, 0, 0, 0] }],
};
export const spendSeries = (ws?: string): Series[] => SPEND_SERIES_BY_WS[ws ?? ""] ?? SPEND_SERIES_BY_WS["trellix-xdr"];

export const RUNS_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const RUNS_SERIES_BY_WS: Record<string, Series[]> = {
  "trellix-xdr": [{ name: "Runs", values: [38200, 41100, 44800, 47200, 52900, 21400, 19800] }],
  "trellix-people": [{ name: "Runs", values: [2600, 2900, 3100, 3300, 3600, 900, 720] }],
  "trellix-grc": [{ name: "Runs", values: [28, 34, 40, 36, 52, 12, 9] }],
  "trellix-labs": [{ name: "Runs", values: [4, 2, 1, 0, 0, 0, 0] }],
};
export const runsSeries = (ws?: string): Series[] => RUNS_SERIES_BY_WS[ws ?? ""] ?? RUNS_SERIES_BY_WS["trellix-xdr"];

export const LATENCY_LABELS = ["Sep 29", "Sep 30", "Oct 1", "Oct 2", "Oct 3", "Oct 4"];
const LATENCY_SERIES_BY_WS: Record<string, Series[]> = {
  "trellix-xdr": [{ name: "p95 latency (ms)", values: [1920, 1880, 1840, 1810, 1860, 1840] }],
  "trellix-people": [{ name: "p95 latency (ms)", values: [820, 800, 780, 760, 790, 760] }],
  "trellix-grc": [{ name: "p95 latency (ms)", values: [2680, 2640, 2600, 2620, 2650, 2640] }],
  "trellix-labs": [{ name: "p95 latency (ms)", values: [0, 0, 0, 0, 0, 0] }],
};
export const latencySeries = (ws?: string): Series[] => LATENCY_SERIES_BY_WS[ws ?? ""] ?? LATENCY_SERIES_BY_WS["trellix-xdr"];

export type Activity = { who: string; action: string; target: string; time: string; tone: "good" | "warning" | "critical" | "neutral" };
const ACTIVITY_BY_WS: Record<string, Activity[]> = {
  "trellix-xdr": [
    { who: "Priya Nair", action: "published", target: "SOC Triage Copilot v7", time: "2026-10-03T16:20:00Z", tone: "good" },
    { who: "System", action: "eval failed for", target: "Incident Report Writer v3", time: "2026-10-04T08:02:00Z", tone: "critical" },
    { who: "Sofia Rossi", action: "re-ingested", target: "Release Notes KB", time: "2026-10-04T06:45:00Z", tone: "neutral" },
    { who: "Diego Fuentes", action: "created", target: "Log Query Builder v2", time: "2026-10-01T17:55:00Z", tone: "neutral" },
    { who: "System", action: "flagged drift watch on", target: "Threat Intel Summarizer", time: "2026-10-02T12:10:00Z", tone: "warning" },
    { who: "Omar Haddad", action: "started a baseline for", target: "Compliance Q&A", time: "2026-10-03T09:25:00Z", tone: "neutral" },
  ],
  "trellix-people": [
    { who: "Sofia Rossi", action: "published", target: "Benefits Q&A v5", time: "2026-10-04T07:10:00Z", tone: "good" },
    { who: "System", action: "eval failed for", target: "Resume Screener v3", time: "2026-10-04T08:28:00Z", tone: "critical" },
    { who: "Priya Nair", action: "published", target: "PTO & Leave Helper v3", time: "2026-10-03T13:40:00Z", tone: "good" },
    { who: "Diego Fuentes", action: "re-ingested", target: "Employee Handbook KB", time: "2026-10-04T05:30:00Z", tone: "neutral" },
    { who: "System", action: "flagged drift watch on", target: "Onboarding Concierge", time: "2026-10-02T15:20:00Z", tone: "warning" },
    { who: "Mei-Ling Chen", action: "started a baseline for", target: "HR Policy Assistant", time: "2026-10-02T09:26:00Z", tone: "neutral" },
  ],
  "trellix-grc": [
    { who: "Omar Haddad", action: "started a baseline for", target: "Compliance Q&A", time: "2026-10-03T09:25:00Z", tone: "neutral" },
    { who: "System", action: "re-ingested", target: "Policy Library KB", time: "2026-10-02T06:00:00Z", tone: "neutral" },
  ],
  "trellix-labs": [
    { who: "Raman Garg", action: "archived", target: "Engineering Onboarding Buddy", time: "2026-08-14T10:00:00Z", tone: "neutral" },
  ],
};
export const activityFor = (ws?: string): Activity[] => ACTIVITY_BY_WS[ws ?? ""] ?? ACTIVITY_BY_WS["trellix-xdr"];

/* Headline stat tiles (illustrative, not derivable from the seed lists) keyed by workspace. */
export type DashStats = { spendDelta: string; evalDelta: string; evalSpark: number[]; callsDelta: string };
const DASH_STATS_BY_WS: Record<string, DashStats> = {
  "trellix-xdr": { spendDelta: "12.4%", evalDelta: "1.2 pts", evalSpark: [91, 92, 90, 93, 94, 93, 95, 96], callsDelta: "18.0%" },
  "trellix-people": { spendDelta: "8.1%", evalDelta: "0.6 pts", evalSpark: [92, 93, 93, 94, 94, 95, 95, 95], callsDelta: "14.2%" },
  "trellix-grc": { spendDelta: "3.2%", evalDelta: "0.0 pts", evalSpark: [84, 84, 85, 84, 85, 85, 84, 85], callsDelta: "5.1%" },
  "trellix-labs": { spendDelta: "0.0%", evalDelta: "0.0 pts", evalSpark: [0, 0, 0, 0, 0, 0, 0, 0], callsDelta: "0.0%" },
};
export const dashStats = (ws?: string): DashStats => DASH_STATS_BY_WS[ws ?? ""] ?? DASH_STATS_BY_WS["trellix-xdr"];

export type RunsStats = {
  runsToday: number; runsTodayDelta: string;
  successDelta: string; successSpark: number[];
  p95ms: number; p95Delta: string;
  spendToday: number; spendTodayDelta: string; spendTodaySpark: number[];
};
const RUNS_STATS_BY_WS: Record<string, RunsStats> = {
  "trellix-xdr": { runsToday: 182940, runsTodayDelta: "+12.4%", successDelta: "-0.3pts", successSpark: [99, 98, 99, 98, 97, 98, 98], p95ms: 1840, p95Delta: "-40ms", spendToday: 214.8, spendTodayDelta: "+6.1%", spendTodaySpark: [120, 140, 160, 150, 180, 200, 215] },
  "trellix-people": { runsToday: 12840, runsTodayDelta: "+9.3%", successDelta: "-0.1pts", successSpark: [98, 99, 98, 99, 99, 98, 99], p95ms: 760, p95Delta: "-18ms", spendToday: 11.6, spendTodayDelta: "+4.4%", spendTodaySpark: [8, 9, 10, 9, 11, 12, 12] },
  "trellix-grc": { runsToday: 210, runsTodayDelta: "+4.0%", successDelta: "-0.2pts", successSpark: [97, 98, 97, 98, 98, 97, 98], p95ms: 2640, p95Delta: "-10ms", spendToday: 6.4, spendTodayDelta: "+2.1%", spendTodaySpark: [1, 2, 2, 3, 3, 4, 4] },
  "trellix-labs": { runsToday: 0, runsTodayDelta: "0.0%", successDelta: "0.0pts", successSpark: [0, 0, 0, 0, 0, 0, 0], p95ms: 0, p95Delta: "0ms", spendToday: 0, spendTodayDelta: "0.0%", spendTodaySpark: [0, 0, 0, 0, 0, 0, 0] },
};
export const runsStats = (ws?: string): RunsStats => RUNS_STATS_BY_WS[ws ?? ""] ?? RUNS_STATS_BY_WS["trellix-xdr"];

export type EvalStats = {
  runs30d: string; runs30dDelta: string; runs30dSpark: number[];
  passRate: string; passRateDelta: string; passRateSpark: number[];
  driftDelta: string; driftSpark: number[];
  qualityDelta: string; qualitySpark: number[];
};
const EVAL_STATS_BY_WS: Record<string, EvalStats> = {
  "trellix-xdr": { runs30d: "142", runs30dDelta: "+23", runs30dSpark: [8, 11, 9, 14, 12, 18, 22], passRate: "90.8%", passRateDelta: "-2.3pts", passRateSpark: [95, 94, 94, 93, 92, 91, 91], driftDelta: "+1", driftSpark: [0, 1, 0, 1, 2, 1, 2], qualityDelta: "+0.8pts", qualitySpark: [88, 89, 90, 89, 90, 90, 91] },
  "trellix-people": { runs30d: "46", runs30dDelta: "+9", runs30dSpark: [3, 4, 4, 6, 5, 7, 9], passRate: "92.4%", passRateDelta: "-0.4pts", passRateSpark: [93, 93, 92, 93, 92, 92, 92], driftDelta: "+1", driftSpark: [0, 0, 1, 0, 1, 1, 1], qualityDelta: "+0.5pts", qualitySpark: [90, 90, 91, 91, 92, 92, 92] },
  "trellix-grc": { runs30d: "6", runs30dDelta: "+2", runs30dSpark: [0, 1, 1, 0, 1, 1, 2], passRate: "84.5%", passRateDelta: "-1.1pts", passRateSpark: [86, 86, 85, 85, 84, 85, 84], driftDelta: "+1", driftSpark: [0, 0, 1, 0, 1, 0, 1], qualityDelta: "+0.3pts", qualitySpark: [83, 84, 84, 85, 84, 85, 85] },
  "trellix-labs": { runs30d: "0", runs30dDelta: "0", runs30dSpark: [0, 0, 0, 0, 0, 0, 0], passRate: "0.0%", passRateDelta: "0.0pts", passRateSpark: [0, 0, 0, 0, 0, 0, 0], driftDelta: "0", driftSpark: [0, 0, 0, 0, 0, 0, 0], qualityDelta: "0.0pts", qualitySpark: [0, 0, 0, 0, 0, 0, 0] },
};
export const evalStats = (ws?: string): EvalStats => EVAL_STATS_BY_WS[ws ?? ""] ?? EVAL_STATS_BY_WS["trellix-xdr"];

/* ------------------------------------------------------------- selectors */
export const getAgent = (id: string) => AGENTS.find((a) => a.id === id);
export const getVersions = (id: string) => VERSIONS[id] ?? [];
export const getKb = (id: string) => KNOWLEDGE_BASES.find((k) => k.id === id);
export const getModel = (name: string) => MODELS.find((m) => m.model === name);

export const statusTone: Record<AgentStatus, "good" | "neutral" | "warning" | "critical"> = {
  published: "good", draft: "neutral", archived: "neutral", eval_failing: "critical",
};
export const statusLabel: Record<AgentStatus, string> = {
  published: "Published", draft: "Draft", archived: "Archived", eval_failing: "Eval failing",
};
export const verdictTone: Record<Verdict, "good" | "critical" | "warning"> = {
  pass: "good", fail: "critical", low_confidence: "warning",
};
export const verdictLabel: Record<Verdict, string> = {
  pass: "Pass", fail: "Fail", low_confidence: "Low confidence",
};
export const driftTone: Record<Agent["drift"], "good" | "warning" | "critical" | "neutral"> = {
  stable: "good", watch: "warning", regressed: "critical", none: "neutral",
};
