/* ============================================================
   Channel registry — declarative definition of each messaging-app channel (Slack / Teams / Google
   Chat): its non-secret config fields, its (multi-secret) credential shape, and the inbound-webhook
   verification scheme that is the trust anchor for the user's identity. The UI renders from this;
   adding a platform is a data change, not new form code. Mirrors connectors.ts.

   @backend GET /v1/channel-defs should return this registry so the UI is driven by what the backend
   actually supports. The inbound adapter itself is P2 (see docs/backend/12-channels.md); this frontend
   config + credential-capture lands ahead of it. [ref: channels-design.md, backend/12-channels.md]
   ============================================================ */

import type { FieldSpec } from "./connectors";

export type ChannelPlatform = "slack" | "teams" | "gchat";

/** How an inbound webhook from the platform is verified — the trust anchor. Since we federate on the
 *  platform's *assertion* of who the user is (rather than a token the user hands us), this check is the
 *  only thing between "@alice really said this" and a spoofed request.
 *   - signing_secret    : HMAC of the raw body + timestamp against a shared secret (Slack)
 *   - bot_framework_jwt : Bot Framework JWT, audience/issuer checked (Teams)
 *   - jwt_audience      : platform-issued bearer JWT, audience must equal the app (Google Chat) */
export type ChannelVerification = "signing_secret" | "bot_framework_jwt" | "jwt_audience";

export type ChannelDef = {
  id: ChannelPlatform;
  label: string;
  configFields: FieldSpec[];
  /** Secret fields. Unlike a connector, a channel can have MORE THAN ONE (e.g. bot token + signing
   *  secret); every `type:"password"` field here is masked into its own CredentialRef. */
  credentialFields: FieldSpec[];
  verification: ChannelVerification;
  /** Short note rendered under the credential UI. */
  credentialHint?: string;
};

export const CHANNEL_DEFS: Record<ChannelPlatform, ChannelDef> = {
  slack: {
    id: "slack",
    label: "Slack",
    verification: "signing_secret",
    configFields: [
      { key: "appId", label: "App ID", type: "text", placeholder: "A0123ABC", required: true },
      { key: "teamId", label: "Workspace (team) ID", type: "text", placeholder: "T0123ABC", required: true },
    ],
    credentialFields: [
      { key: "botToken", label: "Bot user OAuth token", type: "password", placeholder: "xoxb-…", required: true },
      { key: "signingSecret", label: "Signing secret", type: "password", placeholder: "Paste signing secret", required: true },
    ],
    credentialHint: "Bot token + signing secret are stored in the vault; only masked references are kept. The app needs the users:read.email scope so we can map the sender to Okta.",
  },
  teams: {
    id: "teams",
    label: "Microsoft Teams",
    verification: "bot_framework_jwt",
    configFields: [
      { key: "tenantId", label: "Entra tenant ID", type: "text", placeholder: "00000000-0000-0000-0000-000000000000", required: true },
      { key: "botAppId", label: "Bot (app) ID", type: "text", placeholder: "App (client) ID", required: true },
    ],
    credentialFields: [
      { key: "clientSecret", label: "Client secret", type: "password", placeholder: "Paste client secret", required: true },
    ],
    credentialHint: "Client secret is stored in the vault. User identity is federated via Entra on-behalf-of into Okta.",
  },
  gchat: {
    id: "gchat",
    label: "Google Chat",
    verification: "jwt_audience",
    configFields: [
      { key: "projectNumber", label: "Cloud project number", type: "text", placeholder: "123456789012", required: true },
    ],
    credentialFields: [
      { key: "serviceAccountKey", label: "Service-account key", type: "password", placeholder: "Paste JSON key", required: true },
    ],
    credentialHint: "The service-account key is stored in the vault. The sender's verified email maps to Okta.",
  },
};

/** Ordered list for the channel platform <Select>. */
export const CHANNELS: ChannelPlatform[] = ["slack", "teams", "gchat"];

/** All secret fields for a channel (a channel can have several — bot token AND signing secret). */
export function secretFields(def: ChannelDef): FieldSpec[] {
  return def.credentialFields.filter((f) => f.type === "password");
}
