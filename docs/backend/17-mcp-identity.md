# 17 — Per-User MCP Identity (delegated connected accounts)

> **Status: PROPOSED extension (P2+).** A delta on `09-security-tenancy.md`, `12-channels.md`, and
> `14-adoption-enforcement.md §5`. Not yet locked into the schema (`01`) or the frozen API surface (`08`).
> Companion diagram: `docs/diagrams/backend-design/mcp-per-user-connect.html`.

## 1. The problem

Today an MCP server is a **workspace-scoped resource with one credential set** (e.g. a single Jira
token), vaulted under the per-workspace DEK (`09 §9`). Its `access_policy` gates **visibility** — which
roles/teams may `can_use` it (`09 §5`) — but **every caller who can use it reaches the downstream service
as the same identity.** Different users cannot act as *themselves* in Jira/GitHub/Workday; they share one
service identity, so there is no per-user least-privilege and no per-user audit trail downstream.

We want: *"if Atlassian Jira is attached as an MCP, different users use it under their own identity."*

## 2. The model — an MCP `auth_mode`

Mirror the channel `identity_mode` (`12 §4`) with an `auth_mode` on the MCP server:

| `auth_mode` | Downstream identity | Use for | Cost |
|---|---|---|---|
| `shared_service` (default, = today) | one workspace credential | read-only / team-shared tools; **eval-safe** | no per-user audit / least-privilege |
| `per_user` (delegated / connected account) | each caller acts as **themselves** | writes, least-privilege, real downstream audit | one-time OAuth connect; eval tension (§7) |

`auth_mode` is **frozen config**: it rides inside the `tool_bindings` snapshot and is a `DRIFT_TRIGGER`
(`05 §6`) — flipping it forces a re-eval.

## 3. Schema delta (not yet in `01`)

```sql
create type mcp_auth_mode as enum ('shared_service', 'per_user');

alter table mcp_servers
  add column auth_mode mcp_auth_mode not null default 'shared_service';

-- The persisted "user-specific MCP connection": one row per (user, server).
create table mcp_user_connections (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid        not null references workspaces(id),
  mcp_server_id uuid        not null references mcp_servers(id) on delete cascade,
  user_subject  text        not null,                 -- OIDC sub: console principal, channel-federated, or token subject
  vault_path    text        not null,                 -- Vault Transit pointer to the refresh token (per-user DEK) — secret never stored here
  scopes        jsonb       not null default '[]',
  status        text        not null default 'connected',  -- connected | expired | revoked
  connected_at  timestamptz not null default now(),
  last_used_at  timestamptz,
  expires_at    timestamptz,
  unique (mcp_server_id, user_subject)
);
create index on mcp_user_connections (workspace_id, user_subject);
```

- WorkspaceScoped → RLS like every tenant table (`09 §6`). The OAuth refresh token follows the same
  **envelope rule as `provider_keys` / channel creds**: only `{vault_path}` is persisted; the secret is
  decrypted just-in-time into memory and discarded (`09 §9`).
- The connection is keyed `(workspace_id, user_subject, mcp_server_id)` — **connect once, reused across
  future sessions, other channels, and the console.** This is the "user-specific MCP json" persisted so
  later sessions skip the connect step.

## 4. The connect flow (in-chat for channels; inline for console)

A channel caller can't complete an OAuth redirect inside the chat transport, so the agent **replies with a
signed connect link** — effectively "log in to Jira within this session":

1. The run needs a `per_user` MCP tool and finds **no connection** for the caller → fail-closed.
2. The worker delivers an in-chat reply: **`Connect Jira →`** pointing at
   `GET /api/v1/mcp-servers/:id/connect?state=<signed>`.
3. `state` is a **short-lived signed token** binding `{user_subject, workspace_id, mcp_server_id, nonce, exp}`
   — the trust boundary that stops the OAuth callback being bound to the wrong identity (anti-mix-up/CSRF).
4. The user authorizes in the browser (a normal web OAuth to `api`, **out-of-band from chat**).
5. `GET /api/v1/mcp-connect/callback?code=…&state=…` verifies the signed state, exchanges the code for a
   refresh token, vaults it, and **upserts `mcp_user_connections`** (`status=connected`).
6. The user re-asks in chat (**v1**), or the paused run resumes (**v2**, same `interrupt()` mechanism as the
   tool-approval gate, `03 §4`).

The console uses the same routes with an inline "Connect" prompt instead of a chat link.

## 5. Invoke-time resolution

Resolved in the MCP tool node (`services/runtime/graph/nodes.py`), after the existing `can_use` visibility
check (`09 §5`) — the visibility gate is unchanged; this adds the *downstream identity* step:

```python
# ctx.caller_subject(): principal_id (console) | federated subject (channel, 12 §4) | token subject (published, 09)
if binding.mcp and server.auth_mode == "per_user":
    conn = get_connection(server.id, ctx.caller_subject())
    if not conn or conn.status != "connected" or expired(conn):
        return ToolUnavailable(reason="mcp.connect_required",
                               connect_url=signed_connect_url(server, ctx))   # → in-chat link / inline prompt
    token  = vault.decrypt(ctx.workspace_id, conn.vault_path)                 # JIT, memory-only
    client = mcp_client(server, auth=token)                                   # acts AS the user
else:
    client = mcp_client(server, auth=workspace_cred(server))                  # shared_service (today)
```

## 6. Caller-mode interaction (the honest constraint)

| `run_source` | `per_user` behavior |
|---|---|
| `playground` / console | inline connect prompt; resolves to `principal_id` |
| `channel` (Slack/GChat/Teams) | in-chat connect link; resolves to the federated Okta subject (Teams can use **Entra OBO** to skip the manual connect) |
| `api` (published token) | **no interactive path** — works only if the token's subject pre-connected, else `403 mcp.connect_required` (or the server falls back to `shared_service`) |
| `eval` | no real user → see §7 |

## 7. Eval & reproducibility caveat

Eval runs have `principal_id = None` and a `per_user` tool's **result varies by user**, so it is **not
deterministically evaluable**. Resolution: a `per_user` MCP either (a) resolves to a designated
**workspace "eval service connection"** during eval, or (b) its tool path is **excluded from scoring** and
the suite is flagged low-confidence for that capability. `auth_mode` is in `snapshot_hash` (config), but the
per-user *token* is runtime state — so "reproducible" means *reproducible given the same identity*.

## 8. Security

- Refresh tokens are high-value and long-lived → vaulted per `(user, server)`, rotated, **revoked on
  offboarding** (and on `mcp_server` delete via cascade).
- The MCP server's declared **scopes bound** what the delegated token can do; the per-call **egress
  allow-list** still applies at the sandbox boundary (`11 §1`).
- The signed `state` is single-use (nonce) and short-lived; the callback rejects a mismatch.

## 9. Phasing

- **P2.a** — `auth_mode` + `mcp_user_connections` + console connect/callback; `shared_service` stays default.
- **P2.b** — in-chat connect link for channels (reply-with-link + fail-closed), connection reused across surfaces.
- **P2.c** — true OBO token exchange (Entra/Okta → downstream) where the IdP supports it, skipping the manual connect.

---

Cross-refs: `09-security-tenancy.md` (Vault envelope, access, tokens) · `12-channels.md §4` (federation) ·
`14-adoption-enforcement.md §5` (MCP open-registration) · `05-eval-drift.md §6` (drift triggers) ·
`01-data-model.md` (target for the DDL above once locked).
