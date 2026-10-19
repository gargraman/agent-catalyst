/* MCP credential capture — the MCP analog of finalizeChannelCredentials (channel-credentials.ts).
   An auth method may define one or more secret fields; each password field is masked into its own
   CredentialRef. Raw secrets never leave these functions.

   @backend The raw secrets are POSTed once to the vault, which returns { masked, vaultPath } per field;
   this client-side masking is a MOCK stand-in and never persists raw values. [ref: backend/01 §4.6,
   backend/09 §9.1] */

import { genId } from "./store";
import { maskSecret, vaultRef } from "./secrets";
import { MCP_AUTH_METHODS, type McpAuth } from "./mcp";
import type { CredentialRef } from "./data";

/** Mask every secret (password) field for an auth method into its own CredentialRef. */
export function finalizeMcpCredentials(
  auth: McpAuth,
  values: Record<string, string>,
): Record<string, CredentialRef> {
  const out: Record<string, CredentialRef> = {};
  for (const f of MCP_AUTH_METHODS[auth].credentialFields) {
    if (f.type !== "password") continue;
    const raw = (values[f.key] ?? "").trim();
    if (!raw) continue;
    out[f.key] = { masked: maskSecret(raw), vaultPath: vaultRef("mcp", genId("mcp")) };
  }
  return out;
}

/** Every required secret field present? Gates the Connect/Finish button. */
export function mcpCredentialsComplete(auth: McpAuth, values: Record<string, string>): boolean {
  return MCP_AUTH_METHODS[auth].credentialFields.every(
    (f) => !f.required || (values[f.key] ?? "").trim().length > 0,
  );
}
