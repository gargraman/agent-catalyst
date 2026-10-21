/* Tool-binding reference model. An agent's bound tools (`Agent.tools: string[]`) hold either a
   repository-tool name (identity = RepoTool.name) or a qualified MCP-tool ref `mcp:<serverId>/<tool>`.
   resolveToolRef turns a stored ref into a typed, display-ready descriptor — replacing the old
   `name.startsWith("subagent")` heuristic — and degrades gracefully when a ref no longer resolves
   (the tool or server was deleted → `missing`).

   @backend On the backend these are tool_bindings rows (kind + tool_id | mcp_tool_id) frozen into the
   version snapshot; the FE keeps the flat string[] shape and resolves kind by lookup. [ref:
   backend/01 §4.6 tool_bindings] */

import type { McpServer, RepoTool } from "./data";

export const MCP_REF_PREFIX = "mcp:";

/** Badge tone for a tool/MCP-tool risk tier (shared by the tools pages + agent detail). */
export function riskTone(r?: string): "critical" | "warning" | "neutral" {
  return r === "destructive" ? "critical" : r === "write" ? "warning" : "neutral";
}

/** Build a qualified MCP-tool ref for an agent binding. */
export function mcpRef(serverId: string, tool: string): string {
  return `${MCP_REF_PREFIX}${serverId}/${tool}`;
}

export function isMcpRef(ref: string): boolean {
  return ref.startsWith(MCP_REF_PREFIX);
}

/** Split `mcp:<serverId>/<tool>` → { serverId, tool }, or null if not a (well-formed) MCP ref. */
export function parseMcpRef(ref: string): { serverId: string; tool: string } | null {
  if (!isMcpRef(ref)) return null;
  const rest = ref.slice(MCP_REF_PREFIX.length);
  const slash = rest.indexOf("/");
  if (slash <= 0 || slash >= rest.length - 1) return null;
  return { serverId: rest.slice(0, slash), tool: rest.slice(slash + 1) };
}

export type ResolvedToolKind = "HTTP" | "Built-in" | "Sub-agent" | "MCP" | "unknown";

export type ResolvedTool = {
  ref: string;
  kind: ResolvedToolKind;
  label: string; // tool name for display
  description?: string;
  serverName?: string; // MCP only
  riskTier?: "read" | "write" | "destructive";
  /** True when the bound MCP server / HTTP tool uses per-user credentials (doc 17): each caller connects
   *  their own account at runtime rather than a shared service credential. Surfaced at bind time. */
  perUser?: boolean;
  /** True when the ref no longer resolves (deleted tool/server) — render as a removable dangling ref. */
  missing?: boolean;
};

/** Resolve a stored agent-tool ref against the current tool repository + MCP servers. */
export function resolveToolRef(
  ref: string,
  ctx: { tools: RepoTool[]; mcpServers: McpServer[] },
): ResolvedTool {
  const mcp = parseMcpRef(ref);
  if (mcp) {
    const server = ctx.mcpServers.find((s) => s.id === mcp.serverId);
    const tool = server?.tools.find((t) => t.name === mcp.tool);
    if (!server || !tool) {
      return { ref, kind: "MCP", label: mcp.tool, serverName: server?.name, missing: true };
    }
    return {
      ref, kind: "MCP", label: tool.name, description: tool.description,
      serverName: server.name, riskTier: tool.riskTier,
      perUser: server.authMode === "per_user",
    };
  }
  const repo = ctx.tools.find((t) => t.name === ref);
  if (!repo) return { ref, kind: "unknown", label: ref, missing: true };
  return {
    ref, kind: repo.kind, label: repo.name, description: repo.description, riskTier: repo.riskTier,
    perUser: repo.httpConfig?.authMode === "per_user",
  };
}
