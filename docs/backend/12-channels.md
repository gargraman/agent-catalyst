# 12 — Messaging Channels (inbound adapter)

> Implementation-level design for hosting agents behind Slack / Microsoft Teams / Google Chat. The
> product/cross-cutting view is `docs/channels-design.md`; this doc is the backend module. It builds on
> the one-shared-runtime principle (`00 §1`) and the auth planes (`09 §1`). **P2** — lands after the
> published-invoke path (`08`) is solid.

## 1. Principle: a channel is a fourth thin caller

The playground, the published endpoint, and the eval worker all wrap the same
`runtime.invoke(snapshot, inputs, ctx)` (`00 §1`). A channel adapter is the **fourth** such caller. It
adds *ingress + identity federation + reply delivery* around that call — and nothing inside it. The
runtime stays HTTP-free (`00 §1`); all transport lives in `services/api/channels/`.

```mermaid
flowchart LR
  SL[Slack event] --> ING
  TE[Teams activity] --> ING
  GC[Google Chat event] --> ING
  subgraph api["services/api/channels/ (ingress)"]
    ING["verify signature"] --> FED["resolve user → Okta → mint token"] --> RT["route → agent + snapshot"]
  end
  RT --> Q[(Redis queue)]
  Q --> W["worker: runtime.invoke()"]
  W --> REP["post reply to platform"]
  W --> OBS["runs + spans (10)"]
```

**Ack-fast, process-async.** Platforms demand a 2–3s ack. The ingress route verifies + enqueues and
returns `200` immediately; the `worker` runs `invoke()` and posts the reply back out-of-band (the same
split ingestion and evals already use, `00 §2`). The existing P2 outbound "webhook on completion"
(`10 §9`) is the delivery-direction analog.

## 2. Module layout

```
services/api/channels/
  router.py          # POST /channels/{platform}/events  — one ingress route per platform
  verify.py          # per-platform signature/JWT verification (the trust anchor)
  normalize.py       # platform event → InboundMessage (text, thread_id, platform_user, channel_id)
  federate.py        # platform email → Okta subject → minted Agent Platform token (09)
  route.py           # channel + message → (agent_id, version_snapshot)
  deliver.py         # reply back to the platform (buffer SSE → single message, v1)
  install.py         # builder-side: create/connect a Channel, store vaulted creds (08)
```

`federate.py` and `install.py` depend on `09` (auth) and Vault (`11`); everything else is transport. The
`worker` imports `runtime.invoke` + `deliver.post_reply`.

## 3. Data model (`01` delta)

```sql
CREATE TYPE channel_platform AS ENUM ('slack','teams','gchat');
CREATE TYPE channel_status   AS ENUM ('connected','error','disconnected','setup_pending');
CREATE TYPE channel_routing  AS ENUM ('slash','picker','default');
CREATE TYPE channel_identity AS ENUM ('federated','service');

CREATE TABLE channels (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),       -- one channel -> one workspace
  platform      channel_platform NOT NULL,
  name          text NOT NULL,
  status        channel_status NOT NULL DEFAULT 'setup_pending',
  routing       channel_routing NOT NULL DEFAULT 'default',
  identity_mode channel_identity NOT NULL DEFAULT 'federated',
  default_agent_id uuid REFERENCES agents(id),
  okta_group    text,                                          -- channel gate group (coarse end-user gate, §4)
  allowed_email_domains text[] NOT NULL DEFAULT '{}',          -- H1: corporate-domain bind for federation (§4); reject senders outside this set
  service_account text,                                        -- identity_mode='service': the client-credentials principal (§4)
  config        jsonb NOT NULL DEFAULT '{}',                   -- non-secret per-platform config
  credentials   jsonb NOT NULL DEFAULT '{}',                   -- {field: {masked, vault_path}} MULTI-SECRET
  installed_by  uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  last_event_at timestamptz
);

-- one channel -> many agents (routing targets)
CREATE TABLE channel_agents (
  channel_id uuid NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  agent_id   uuid NOT NULL REFERENCES agents(id)   ON DELETE CASCADE,
  slash      text,                                             -- e.g. '/triage' (routing='slash')
  PRIMARY KEY (channel_id, agent_id)
);

-- H1.5: inbound-event dedup + dead-letter. The platform's event id is recorded BEFORE enqueue; a duplicate
-- (platform retry) hits the PK and is dropped → one event id = one runtime.invoke. `status` drives the
-- dead-letter path so an acked-but-unprocessed event is retried/surfaced, not silently lost.
CREATE TABLE channel_events (
  channel_id     uuid NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  platform_event_id text NOT NULL,                             -- the platform's unique event/message id
  status         text NOT NULL DEFAULT 'received',            -- received | processed | dead_letter
  received_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_id, platform_event_id)                  -- dedup key: second delivery of the same id is a no-op
);
```

- `credentials` is a **multi-secret** map (bot token + signing secret), each value a `{masked,vault_path}`
  ref — the raw secret is POSTed once to Vault on install and never returned (same envelope as
  `provider_keys`, `09 §8`). This is the backend of the frontend's `Record<string, CredentialRef>`.
- RLS applies as to every tenant-owned table (`09 §7`): `workspace_id` is the partition.
- **`run_source`** enum gains `'channel'` (or per-platform values); **`subject_type` is unchanged**
  (`user` for a federated human, `service` for a service account) — the Okta model needs no new subject
  class. `runs.caller` carries the platform handle for attribution.

## 4. Identity federation (`09` delta)

The **channel arm of the Caller (`executer`) plane** (`09 §1`, claim-based 2026-10-22): the caller-token and
channel ingresses are **one plane** — both resolve to the single execute-only role `executer`, differing only
in *how* identity is sourced (a bearer token vs. the verified-email→Okta federation below):

```python
CHANNEL_INVOKE_ROLE = "executer"   # the caller plane's single execute-only role (09 §1); see "Role & membership"

# `idp` is the active IdentityProvider adapter (18) — no `okta.*` calls here; federation goes through the contract.
def resolve_channel_principal(ch: Channel, msg: InboundMessage, idp: IdentityProvider) -> MintedToken:
    if ch.identity_mode == "service":
        return mint_service_token(ch.service_account)          # client-credentials
    # H1 — email is NON-AUTHORITATIVE on its own. Signature verification proves the event came from the
    # platform intact, NOT that the sender owns the email. So before using email as the join key:
    if not msg.platform_user.email_verified:                   # platform JWT claim (Google/Entra); reject if absent/false
        raise UnverifiedEmail(msg.platform_user.email)         # -> friendly in-chat denial
    email = msg.platform_user.email
    if domain_of(email) not in ch.allowed_email_domains:       # corporate-domain bind; blocks guest/Slack-Connect/personal
        raise ExternalSenderRejected(email)                    #   collisions that would otherwise federate to an internal subject
    sub = idp.resolve_subject(email)                           # adapter: verified email -> subject (18 §2)
    if sub is None:
        raise Unmapped(email)                                  # -> friendly in-chat denial
    groups = idp.groups_for(sub)                               # adapter: the user's IdP groups (= teams)
    if ch.okta_group and ch.okta_group not in groups:         # COARSE GATE — the channel's gate group; enforce before mint
        raise NotInChannelGroup(email)                         # -> friendly in-chat denial
    if ch.platform == "teams":
        # Teams OBO is the Entra adapter's exchange_obo (18); other adapters raise unsupported. The exchanged
        # token must resolve to the same {CHANNEL_INVOKE_ROLE, teams = IdP groups}.
        return idp.exchange_obo(msg.platform_token, audience=IDP_AUD)   # adapter token exchange
    # Direct mint (slack, gchat): executer role, teams = the user's IdP groups.
    return mint_platform_token(sub, workspace=ch.workspace_id, role=CHANNEL_INVOKE_ROLE, teams=groups)
```

The minted token feeds the unified `can_use(policy, *, role, teams, subject)` (`09 §5`) exactly like a cURL
caller token — Okta groups are the `teams`, the **same model the console uses** (`09 §2.1`). A channel
caller has no owner `subject`, so a `private` agent is denied — and because `executer` is a caller role, not
`admin`, there is **no "admin token" bypass at invoke** (`09 §5`). Note the channel's `okta_group` (this
gate) is distinct from a workspace's membership claims (the per-workspace role claims mapped by `idp_binding`, `01`/`09
§2.1`): the channel group gates *this channel's* end users at invoke, the workspace claims confer *console*
membership/role — a channel user needs neither.
**End-user access = two gates:** the channel `okta_group` (coarse — checked above, before mint) ∩ the
agent's `access_policy` (fine — evaluated in `resolveRun`). Both must pass.

> **H1 — email is a non-authoritative join key; hardened platform tenant is a precondition.** A valid event
> signature proves provenance, **not** email ownership — so without the checks above, any external/guest/
> Slack-Connect/personal account whose email **collides** with an internal subject would federate straight into
> that subject with full agent access. Mitigations, all required: (1) verify an **`email_verified`** / domain-
> bound claim and reject if absent/false; (2) **bind federation to the corporate email domain(s)**
> (`ch.allowed_email_domains`), rejecting guests/Slack-Connect; (3) install the platform workspace as
> **no-guests, corporate-domain-only** (a deployment precondition, documented, not just code). Negative test
> (09 §8 / channel suite): a colliding external/unverified email is **rejected**, never federated.
>
> **Inbound idempotency (H1.5).** Dedupe on the **platform event id** before enqueue, with a **dead-letter**
> path, so an acked-but-unprocessed event isn't silently dropped and platform **retries don't double-invoke**
> the agent. A duplicate event id → a single `runtime.invoke`.

> **Teams OBO (H1 side-issue, resolved by the adapter).** The non-standard Entra→Okta cross-IdP exchange is now
> the Entra adapter's `exchange_obo` (`18`); `teams` come **uniformly** from `idp.groups_for` regardless of
> platform, so the old "OBO breaks teams = Okta groups" concern is gone — all three platforms resolve `teams`
> the same way.

**Role & membership.** The channel arm is **membership-independent** (`09 §1`): a federated end user needs
**no workspace role claim** at all (unlike the console `get_workspace_ctx` guard, which requires an
`ap-<prefix>-{builder,publisher}` claim, `09 §3`). The minted `role` is always **`executer`**
(`CHANNEL_INVOKE_ROLE`) — the caller plane's execute-only role; invoke requires no console privilege, so any
`useRoles`-based policy treats channel users as `executer` and real authorization rides on `teams` (Okta
groups) ∩ the agent's policy. Role is **not** derived from Okta groups (consistent with the console: the
*functional* groups → teams, not roles). Tenancy still holds: the channel is pinned to one `workspace_id` and
RLS (`09 §7`) partitions every query, so a channel user can reach only **that** workspace's bound agents.

## 5. Invoke mapping (`03`)

```python
inbound → InvokeInputs(messages=[{"role":"user","content": msg.text}],
                       session_id=f"{ch.platform}:{msg.thread_id}")   # NEW TURN ONLY (03 open contract)
ctx = RunContext(workspace_id=ch.workspace_id, run_source="channel",
                 principal_id=sub, caller_token_id=None, trace_id=…, deadline_s=30)
```

Pin the new-turn-vs-full-history contract (`session-memory-status.md`) before shipping: channels deliver
one turn at a time, so **new-turn** is both correct and the only option that doesn't double-count the
`messages` append-reducer (`03 §state`).

## 6. Security (`09 §10`, `11`)

- **Signature verification is the sole proof of identity** (we trust the platform's assertion, not a
  user-presented token). Per `ChannelDef.verification`: Slack HMAC over raw body+timestamp (replay
  window), Teams Bot Framework JWT (audience/issuer), Google Chat Google JWT (audience = app). Reject on
  any mismatch; rate-limit per channel.
- Inbound untrusted text → model → tools is the named dangerous surface (`09 §10`): egress allow-lists
  apply; no channel faces outside the org until a written threat model is signed off (`09 §12`).
- Strongest case yet to split public invoke into its own Deployment for blast-radius isolation (`00 §2`).

## 7. Observability (`10` delta)

`run_source='channel'` added to the per-call log, the Prometheus labels (`workspace_id, agent_id,
run_source, model`), and Langfuse metadata. Per-user cost attribution now works because the run carries a
real Okta `principal_id` — a first for external-origin traffic.

## 8. Phasing

- **P2.a:** Slack only, `routing='default'` (one channel → one default agent), federated identity,
  buffer-to-final reply. Proves the ingress + federation + reply loop end-to-end.
- **P2.b:** Teams (Entra OBO) + Google Chat; slash/picker routing; per-platform native formatting;
  edit-to-stream.

→ Related: `00` (callers), `03` (invoke/session), `08` (API surface), `09` (auth/tenancy), `10` (obs).
