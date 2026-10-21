/* Per-user OAuth "app" credential capture — shared by MCP servers and HTTP tools whose authMode is
   "per_user" (doc 17). When a resource requires per-user credentials, the builder still registers ONE
   shared OAuth *app* (client id/secret + scopes) that the platform owns; each end user then connects
   their own account against that app at runtime. The per-user tokens live in a separate connection store
   (mcp_user_connections, doc 17 §3), NOT here — see runtime-access.ts for the connect-required preview.

   @backend The raw app secret is POSTed once to the vault, which returns { masked, vaultPath } per field;
   this client-side masking is a MOCK. App creds are vaulted under the platform/per-workspace DEK
   (09 §9.1); per-user tokens are a runtime concern (doc 17 §4–§6), never captured here. */

import { genId } from "./store";
import { maskSecret, vaultRef } from "./secrets";
import type { FieldSpec } from "./connectors";
import type { CredentialRef } from "./data";

/** The OAuth app registration fields captured when a server/tool uses per-user credentials.
 *  Both are vaulted as CredentialRefs in the mock (the client id is not strictly secret, but keeping the
 *  whole set in one masked map keeps the shared/per-user code paths uniform). */
export const PER_USER_OAUTH_APP_FIELDS: FieldSpec[] = [
  { key: "clientId", label: "OAuth client ID", type: "password", required: true, placeholder: "e.g. Iv1.ab12cd34…", hint: "From the OAuth app you registered with the provider" },
  { key: "clientSecret", label: "OAuth client secret", type: "password", required: true, placeholder: "the app's client secret" },
];

/** Non-secret scopes field, captured alongside the app creds and stored in plaintext as `oauthScopes`. */
export const PER_USER_OAUTH_SCOPES_FIELD: FieldSpec = {
  key: "scopes", label: "Scopes", type: "text", placeholder: "e.g. repo, read:user",
  hint: "Requested from each user's OAuth grant",
};

/** Mask the OAuth app secret fields into CredentialRefs; return the scopes string separately. */
export function finalizeOauthApp(values: Record<string, string>): {
  refs: Record<string, CredentialRef>;
  scopes?: string;
} {
  const refs: Record<string, CredentialRef> = {};
  for (const f of PER_USER_OAUTH_APP_FIELDS) {
    const raw = (values[f.key] ?? "").trim();
    if (!raw) continue;
    refs[f.key] = { masked: maskSecret(raw), vaultPath: vaultRef("oauth-app", genId("oauth")) };
  }
  const scopes = (values.scopes ?? "").trim();
  return { refs, ...(scopes ? { scopes } : {}) };
}

/** Required OAuth app fields present? Gates the Connect/Finish button when authMode is per_user. */
export function oauthAppComplete(values: Record<string, string>): boolean {
  return PER_USER_OAUTH_APP_FIELDS.every((f) => !f.required || (values[f.key] ?? "").trim().length > 0);
}
