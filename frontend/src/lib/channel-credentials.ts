/* Channel credential capture — the MULTI-SECRET analog of finalizeCredential (source-picker.tsx).
   A channel can have several secrets at once (e.g. Slack bot token AND signing secret), so each
   password field is masked into its own CredentialRef. Raw secrets never leave these functions.

   @backend The raw secrets are POSTed once to the vault, which returns { masked, vaultPath } per field;
   this client-side masking is a MOCK stand-in and never persists raw values. [ref: backend/12-channels.md §3] */

import { genId } from "./store";
import { maskSecret, vaultRef } from "./secrets";
import { CHANNEL_DEFS, type ChannelPlatform } from "./channels";
import type { CredentialRef } from "./data";

/** Mask every secret (password) field for a channel into its own CredentialRef. */
export function finalizeChannelCredentials(
  platform: ChannelPlatform,
  values: Record<string, string>,
): Record<string, CredentialRef> {
  const def = CHANNEL_DEFS[platform];
  const out: Record<string, CredentialRef> = {};
  for (const f of def.credentialFields) {
    if (f.type !== "password") continue;
    const raw = (values[f.key] ?? "").trim();
    if (!raw) continue;
    out[f.key] = { masked: maskSecret(raw), vaultPath: vaultRef("channel", genId(platform)) };
  }
  return out;
}

/** Every required config field present? */
export function channelConfigComplete(platform: ChannelPlatform, fields: Record<string, string>): boolean {
  return CHANNEL_DEFS[platform].configFields.every((f) => !f.required || (fields[f.key] ?? "").trim().length > 0);
}

/** Every required secret field present? Gates the Connect button. */
export function channelCredentialsComplete(platform: ChannelPlatform, values: Record<string, string>): boolean {
  return CHANNEL_DEFS[platform].credentialFields.every((f) => !f.required || (values[f.key] ?? "").trim().length > 0);
}
