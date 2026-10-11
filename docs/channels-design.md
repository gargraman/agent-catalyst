# Messaging-app channels — design & impact

> Let people **talk to hosted agents from Slack / Microsoft Teams / Google Chat** instead of calling
> `POST /v1/agents/:id/invoke` with cURL/TS/Python. Agent Catalyst becomes the host; the chat app is the
> client. This doc is the cross-cutting design + impact; the backend module spec is `backend/12-channels.md`.
>
> Read `scope.md` → `spec.md` → `backend/` first. This extends `spec.md §7 Export & integration`, which
> today stops at code-snippet export. Nothing here contradicts the locked stack.

## 1. What a channel is

A **Channel** is a connected messaging-app installation (one Slack workspace / one Teams tenant / one
Google Chat space-set) bound to **one Agent Catalyst workspace** and routing to **many agents** inside
it. It is a new *distribution surface* on top of the existing published runtime — not a new runtime.

```
one channel install ──▶ one workspace (tenant) ──▶ many agents (routed per message)
```

Locked decisions (see the approved plan): platforms = **Slack, Teams, Google Chat** (Discord dropped);
end users = **internal employees in one Okta** (`trellix.okta.com`); identity = **federate + mint
server-side**; binding = **one channel → one workspace → many agents**.

## 2. Identity — federate, don't forward (the load-bearing part)

Each platform authenticates the user against **its own** IdP and hands the bot **its own** credential
(a Google/Entra/Slack token whose audience is the bot) — never an Okta token, never valid at Catalyst.
So we federate on the *verified identity*, and mint our own token:

1. **Verify the inbound webhook signature** — the trust anchor. Since we trust the platform's assertion
   of *who* the user is (rather than a token the user hands us), this check is the only thing between
   "@alice really said this" and a spoofed request. Per-platform scheme — see §5.
2. **Read the platform-asserted verified email** + thread id + routed agent. Requires the platform to
   expose email (Slack needs the `users:read.email` scope) and for it to match the Okta identity.
3. **Mint server-side.** email → Okta subject → short-lived Catalyst token carrying
   `{subject, workspace, role, teams(=Okta groups), scopes}`. Use true token exchange where the platform
   supports it (**Teams → Entra on-behalf-of**); otherwise mint directly for the mapped Okta subject
   after verifying the asserted email (**Slack, Google Chat**). Service-to-service uses an **Okta service
   account** (client-credentials).
4. **Resolve + invoke** as that principal (§3).

**Consequence — no new subject type.** A federated human is `subject_type: user`; a service account is
`service`. The channel is a new **run source**, not a new identity class. Okta groups map directly onto
the existing `teams` in `access-policy.ts`, so `canUse`/`canManage` work unchanged. The "external
identity" problem only existed when users were anonymous; the Okta decision removes it.

**End-user access = two gates.** The channel's **Okta group** (coarse: who may reach this channel at
all) **∩** the **agent's own `accessPolicy`** evaluated in `resolveRun` (fine: per-agent). Both must pass.

**Unmapped user** (verified email not in Okta): deny with a friendly in-chat message. (No silent
fallback to a service identity — that would let an unknown user borrow service scopes.)

## 3. Invoke / session flow (reuses the runtime verbatim)

A channel adapter is a **fourth thin caller** of `runtime.invoke(snapshot, inputs, ctx)`, alongside the
playground, the published endpoint, and the eval worker — so channel traffic runs the exact code evals
measure.

```
platform event ─▶ [verify sig] ─▶ [resolve user→Okta, mint token] ─▶ [route→agent]
               ─▶ runtime.invoke(snapshot, {messages:[new turn], session_id}, ctx)
               ─▶ buffer SSE ─▶ post reply to platform ─▶ record Run
```

- `session_id` = the platform thread id → the LangGraph checkpointer keyed `(workspace, agent, session)`
  gives conversation continuity for free.
- **Session contract (must pin, `backend/session-memory-status.md`):** on a continuing session send
  **only the new turn**. Messaging delivers one turn at a time — the clean fit; re-sending full history
  double-counts the append-reducer.
- `resolveRun` intersection (agent config ∩ caller grants ∩ tenant ∩ policy) is channel-agnostic —
  reused as-is.
- SSE → **buffer to a single reply** (v1). Edit-to-stream and markdown→platform-native rendering
  (Slack blocks / Teams adaptive cards) are follow-ups.

## 4. Entities

Mirror the `connectors.ts` declarative registry + the workspace-tagged resource pattern.

```ts
export type ChannelPlatform = "slack" | "teams" | "gchat";

export type ChannelDef = {              // declarative per-platform spec; adding a platform is a data change
  id: ChannelPlatform; label: string;
  configFields: FieldSpec[];            // reuse FieldSpec from connectors.ts (app id, team/space id, …)
  credentialFields: FieldSpec[];        // MULTI-SECRET: e.g. Slack = bot_token + signing_secret
  verification: "signing_secret" | "jwt_audience" | "bot_framework_jwt";
  credentialHint?: string;
};

export type Channel = {
  id: string; name: string; platform: ChannelPlatform;
  status: "connected" | "error" | "disconnected" | "setup_pending";
  workspaceId?: string;                 // one channel -> one workspace (tenant boundary)
  agentIds: string[]; defaultAgentId?: string;
  routing: "slash" | "picker" | "default";   // how a message selects an agent (§6)
  identityMode: "federated" | "service";
  oktaGroup?: string;                   // Okta group granted this channel's workspace
  credentials: Record<string, CredentialRef>; // MULTI-SECRET (extends today's single-ref model)
  webhookUrl?: string;                  // Catalyst ingress URL registered in the platform
  installedBy: string; createdAt: string; updatedAt: string; lastEventAt?: string;
  accessPolicy?: AccessPolicy;          // who may MANAGE this binding (builders/admins)
};
```

Two deliberate extensions of existing models:

- **Multi-secret credentials.** Slack/Teams need bot token *and* signing secret. Today
  `finalizeCredential`/`secretField` vault exactly one secret. Channels mask **every** `type:"password"`
  field into `credentials: Record<fieldKey, CredentialRef>`.
- **Routing.** One install = one app = one bot user, so `@bot` can't disambiguate agents. Selection is a
  **slash-command per agent** or an **in-chat picker**, plus a **default agent**. (`@mention a named
  agent` only works with one-bot-per-agent, which collapses to one→one, so it's excluded.) v1: default
  agent + slash-command.

## 5. Per-platform specifics

| Platform | Signature / verification | Email source | Token mint | Reply |
|---|---|---|---|---|
| **Slack** | `signing_secret` (HMAC of raw body + timestamp) | `users.info` (needs `users:read.email`) | direct mint for mapped Okta subject | `chat.postMessage` |
| **Teams** | `bot_framework_jwt` (Bot Framework JWT, audience check) | AAD claim | **Entra on-behalf-of** token exchange | Bot Framework reply activity |
| **Google Chat** | `jwt_audience` (Google-issued bearer JWT, verify audience = app) | event `sender.email` | direct mint for mapped Okta subject | space message / webhook |

## 6. Impact

### Frontend (mock)
| Area | File(s) | Change |
|---|---|---|
| Channel registry | new `src/lib/channels.ts` | `ChannelDef`, `CHANNEL_DEFS`, `CHANNELS`; reuse `FieldSpec`. Mirrors `connectors.ts`. |
| Entity + seed | `src/lib/data.ts` | `Channel`/`ChannelPlatform` + `CHANNELS` seed; reuse `CredentialRef`. `Run.source` += channel value. |
| Store | `src/lib/store.ts` | `channels` slice, `useChannels`/`useChannel`, `addChannel`/`updateChannel`/`deleteChannel`, `setAccessPolicy` kind `+ "channel"`, net-new `addRun`. |
| Multi-secret capture | new `finalizeChannelCredentials` | mask every password field → `Record<key, CredentialRef>`. |
| Access resolution | `src/lib/runtime-access.ts` | `RunCtx` channel arm + `resolveRun` branch (federated→user, service→service); intersection/policy reused; **no `subjectType` widening**. |
| Observability | `runs/page.tsx` | source filter, badge color, caller fallback for channels. |
| Channels UI | new `(app)/channels/{page,[id],new}` + nav | dedicated section (list/detail/new). ⚠️ `frontend/AGENTS.md`: read `node_modules/next/dist/docs/` before new route code. |
| Agent cross-link | `agents/[id]/page.tsx` Publish tab | read-only "Reachable via channels". |

### Backend (design delta — see `backend/12-channels.md`)
New `services/api/channels/` module (ingress + verify + route + federate + reply), a fourth caller of
`runtime.invoke()`; identity federation in `09`; a `channels` table + `run_source` value in `01`; channel
metrics in `10`; the pinned session contract in `03`. `subject_type` is unchanged.

## 7. Open decisions (recommendation first)
1. **Channels home:** dedicated `/channels` section *(recommended)* vs Settings tab.
2. **Routing:** default agent + slash-command *(recommended)* vs in-chat picker.
3. **Reply rendering:** buffer-to-final *(recommended)* vs edit-to-stream; platform-native formatting is a follow-up.
4. **Unmapped Okta user:** deny with a friendly message *(recommended)* vs service-identity fallback.

## 8. Security notes
- Webhook signature verification is security-critical — it is the sole proof of user identity.
- Inbound widens the untrusted-input → tool-execution surface (`09 §10`); threat-model sign-off before
  any channel faces outside the org.
- Rate-limit the public ingress; strongest case yet to split public invoke into its own Deployment
  (blast-radius isolation, `00 §2`).
