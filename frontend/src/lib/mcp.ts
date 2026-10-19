/* ============================================================
   MCP auth registry — declarative definition of each MCP-server authentication method and the
   (vaulted) credential it captures. The wizard renders credential inputs from this, so adding an
   auth method is a data change, not new form code. Mirrors connectors.ts / channels.ts.

   NOTE: this registry unifies the *MCP transport* auth vocabulary only. HTTP-tool auth
   (HttpToolConfig.auth in data.ts) and datasource-connector auth (connectors.ts AuthMethod) are
   genuinely different domains and are intentionally NOT merged here.

   @backend GET /v1/mcp-auth-methods should return this registry so the UI is driven by what the
   backend's MCP client actually supports. The raw credential is POSTed once to the vault, which
   returns { masked, vaultPath }; only the masked ref is persisted. [ref: backend/01-data-model.md
   §4.6 mcp_servers.config (auth ref vaulted), spec B7] */

import type { FieldSpec } from "./connectors";

/** MCP-server authentication method — the discriminant the credential UI switches on. Creds are
 *  vaulted and injected at connect (spec B7); `none` captures nothing. */
export type McpAuth = "none" | "bearer" | "oauth" | "headers";

export type McpAuthMethod = {
  label: string;
  /** Secret fields captured for this method; every type:"password" field is masked into its own
   *  CredentialRef (see mcp-credentials.ts). Empty for `none`. */
  credentialFields: FieldSpec[];
  /** Short note rendered under the credential UI. */
  credentialHint?: string;
};

export const MCP_AUTH_METHODS: Record<McpAuth, McpAuthMethod> = {
  none: {
    label: "None",
    credentialFields: [],
  },
  bearer: {
    label: "Bearer token",
    credentialFields: [
      { key: "token", label: "Bearer token", type: "password", placeholder: "Paste token", required: true },
    ],
    credentialHint: "Stored in the vault; only a masked reference is kept.",
  },
  oauth: {
    label: "OAuth 2.0",
    credentialFields: [
      { key: "accessToken", label: "Access token", type: "password", placeholder: "Paste token", required: true },
    ],
    credentialHint: "Simulated OAuth — no real redirect. The token is vaulted; only a masked reference is kept.",
  },
  headers: {
    label: "Custom headers",
    credentialFields: [
      { key: "headerValue", label: "Auth header value", type: "password", placeholder: "e.g. the value for X-API-Key", required: true },
    ],
    credentialHint: "The header value is vaulted; only a masked reference is kept.",
  },
};

/** Ordered list for the auth-method <Select>. */
export const MCP_AUTHS: McpAuth[] = ["none", "bearer", "oauth", "headers"];

/** Secret (password) fields for an auth method — what gets masked into CredentialRefs. */
export function mcpSecretFields(auth: McpAuth): FieldSpec[] {
  return MCP_AUTH_METHODS[auth].credentialFields.filter((f) => f.type === "password");
}

/** Display label for a stored auth value (replaces the old free-text AUTH_LABELS map). */
export function mcpAuthLabel(auth: McpAuth): string {
  return MCP_AUTH_METHODS[auth]?.label ?? auth;
}
