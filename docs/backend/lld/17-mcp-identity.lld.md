# 17 (LLD) — Per-User MCP Identity (delegated connected accounts)

> **Parent:** [`../17-mcp-identity.md`](../17-mcp-identity.md) (the `auth_mode` model, the
> `mcp_user_connections` DDL, the in-chat connect flow, the invoke-time resolution sketch, the caller-mode
> table, the eval caveat, token lifecycle, phasing).
> **Status: PROPOSED extension (P2+)** — a delta on `09`/`12`/`14`. The per-user *feature* stays P2+ and is not
> yet built, but the two cross-doc reconciliation items it flagged have **now been folded** (2026-10-25 pass):
> the `01` LLD §4.2 `snapshot_hash` MCP tool-binding entry carries `auth_mode` + `mcp_scopes` (§7), and the
> `08 §1` error table carries the `mcp.connect_required` row (§8). `caller_subject()` is now a method on
> `RunContext` (`01` LLD §2, §3). The parent fixes **the model and the decisions**; it leaves load-bearing mechanisms at HLD
> altitude: the **on-behalf-of decision is leaned-toward but never pinned**, `ctx.caller_subject()` is *called*
> but no such method exists on `RunContext` (`01` LLD §2), shared-service credential storage/injection is prose,
> the signed-state crypto + nonce single-use is unspecified, and the mid-stream-vs-preflight error split is
> undefined. This doc makes those buildable. **Design only — nothing built.**

## What this adds over parent `17`

| Gap in `17` | Filled here |
|---|---|
| on-behalf-of is implied (`per_user` = "act as themselves") but never **pinned** as a decision with the downstream-identity contract | §1 — the **locked** OBO decision: delegated-grant (not impersonation/token-exchange by default); what identity the MCP server sees per `auth_mode`; relation to `18` + the P2.c exchange |
| shared-service auth ("one workspace credential, vaulted") — storage scope, the `mcp.ts` 4 auth methods, injection | §2 — the 4 auth methods → per-workspace DEK (`09` LLD §3) + the `CredentialRef` model + injection at connect |
| `ctx.caller_subject()` called (`17 §5`) but **undefined** on `RunContext` (`01` LLD §2) | §3 — the `caller_subject()` method across the 3 planes (amendment to `01` LLD §2 / `00 §4`), tied to `18` |
| invoke-time resolution is a sketch (`17 §5`): no refresh grant, no user-DEK decrypt, no rotation re-vault | §4 — the refined MCP tool-node body (connection lookup, expiry/refresh, per-user DEK, rotating re-vault) |
| connect flow (`17 §4`): the signed `state` crypto, nonce single-use, callback idempotency are named not built | §5 — HMAC state + Redis nonce (single-use), the two routes, the idempotent callback upsert |
| trust/allowlist + per-workspace isolation stated across `14 §5`/`09`; not assembled | §6 — open-registration + egress + access_policy + RLS, assembled; the `per_user` × tenant isolation |
| data-model + snapshot-hash deltas; the `auth_mode`-is-a-DRIFT_TRIGGER claim not reconciled with the `01` LLD hash | §7 — enum/column/table/DEK deltas + the **`auth_mode`→`snapshot_hash` flag** (`01` LLD §4.2 omits it today) |
| error taxonomy: `mcp.connect_required` as "403" vs a streaming run — the category split is undefined | §8 — preflight 403 (cat. A) for non-interactive vs in-node connect affordance (cat. B); refresh-failure handling; eval |

Python 3.12, `from __future__ import annotations`, SQLAlchemy **`AsyncSession`** on the invoke/worker path
([`00-conventions.lld.md §4`](00-conventions.lld.md)). SQL is PostgreSQL 16. Snippets compile (§9). `VaultEnvelope`
(`09` LLD §3), `RunContext`/`Principal` (`01` LLD §2), `AccessDenied` (`00` LLD §1), `can_use` (`09 §5`), the MCP
tool node + per-tool error model (`03` LLD §4/§5), the identity adapter (`18` LLD): **see those — not restated.**

---

## 1. The on-behalf-of decision (locked)

The parent leans toward it; this **pins it** so the module can be built without re-litigating:

> **Decision (locked).** `per_user` MCP is **on-behalf-of the end user via a delegated OAuth *connected
> account*** — the user explicitly grants our platform a scoped OAuth refresh token (the connect flow, §5), and
> we call the downstream service with an access token minted from *that* grant. The platform does **not**
> impersonate (no "act-as" API), and it does **not** use IdP→downstream token-exchange (true OBO) as the
> default. Token-exchange OBO is **P2.c only**, where the IdP supports it (`18 exchange_obo`, e.g. Entra→Teams),
> as an *optimization that skips the manual connect* — the identity model is identical.

What the downstream server (Jira/GitHub/Workday) **sees**, per `auth_mode`:

| `auth_mode` | Downstream identity | Credential | DEK scope (09 LLD §3) |
|---|---|---|---|
| `shared_service` (default) | the **workspace service account** — one identity for every caller | the fixed vaulted cred (§2) | **per-workspace** `ws-<workspace_id>` |
| `per_user` | the **end user's own** downstream account (their Jira user, their GitHub) | that user's delegated OAuth refresh token (§4) | **per-user** `user-<subject_hash>` |

This is what buys per-user least-privilege + a real downstream audit trail (`17 §1`): because the call carries
the user's own grant, the downstream service authorizes and logs *them*, not a shared bot. The identity is the
**`18`-adapter-derived subject** (§3) — so `per_user` MCP is not a new identity system, it is the same
`Principal` the console/channel/token planes already resolve, extended with a connected-account grant.

---

## 2. Authenticating TO MCP servers — `shared_service` (fills `17 §1`)

The default path (today): one workspace credential, vaulted, used for every caller. The `mcp.ts`
`McpAuth` vocabulary (`none|bearer|oauth|headers`) is the capture surface; each `type:"password"` field is a
`CredentialRef` `{masked, vault_path}` (`mcp-credentials.ts`, `secrets.ts`), raw secret write-only to Vault.

| `McpAuth` (mcp.ts) | Stored (per-workspace DEK) | Injected at connect |
|---|---|---|
| `none` | nothing | — |
| `bearer` | the token | `Authorization: Bearer <token>` |
| `oauth` | the access token (shared-service, non-delegated) | `Authorization: Bearer <token>` |
| `headers` | the header value(s) | the configured custom header(s) |

```python
async def shared_cred(server, ctx, *, vault: "VaultEnvelope", db) -> dict[str, str]:
    """Decrypt the workspace-scoped MCP credential JIT, return the headers to inject. Per-workspace DEK
    (09 LLD §3): only {wrapped_dek,nonce,ct,key} is persisted; plaintext lives only for this call (09 §9)."""
    env = server.config["credentials"]                       # {field: VaultEnvelope} (01 §4.6, mcp_servers.config)
    out: dict[str, str] = {}
    for field, cred_env in env.items():
        secret = (await vault.decrypt("workspace", ctx.workspace_id, cred_env)).decode()
        out[field] = secret
    return _as_headers(server.auth, out)                     # bearer→Authorization; headers→custom; oauth→Authorization
```

- **Scope = per `(workspace, mcp_server)`** — the credential lives on the `mcp_servers` row (RLS-tenant-scoped,
  `09` LLD §2), decrypted under the **per-workspace** DEK. A caller in tenant B can neither see nor decrypt
  tenant A's MCP cred (the `09 §8` secrets isolation test, part b).
- **`per_user` servers also have a workspace-scoped secret: the OAuth *app* client.** When `auth_mode=per_user`,
  `mcp_servers.config.credentials` holds the **workspace's registered OAuth client** (client id + secret,
  per-workspace DEK) used to *run the OAuth dance*; each **user's refresh token** is the separate per-user secret
  (§4, §7). Two distinct secrets, two DEK scopes — do not conflate (this is not spelled out in the parent; it is
  load-bearing).

---

## 3. Caller-identity propagation — `ctx.caller_subject()` (fills `17 §5`)

The parent's invoke snippet calls `ctx.caller_subject()`, but **no such method exists** on `RunContext`
(`01` LLD §2 has `principal_id`, `caller_token_id`, `principal`). This defines it — one derivation across the
three planes, each sourcing its subject from the `18` adapter:

```python
# AMENDMENT to RunContext (01 LLD §2 / 00 §4): add this method. principal.subject is the resolved identity:
#   console   → principal_id (OIDC sub) / principal.subject (18 adapter build_principal)
#   channel   → the federated subject minted from the end user's email→subject (12 §4, idp.resolve_subject)
#   api token → the caller token's subject claim (09 §6 resolve_caller: tok.scopes.get("subject"))
def caller_subject(self) -> str | None:
    if self.principal is not None and self.principal.subject:
        return self.principal.subject
    return self.principal_id            # console fallback; None for a subject-less token/service caller
```

`None` is a first-class outcome: a **service** channel account (`12 §4`) and a **subject-less** cURL token have
no connectable identity, and an **eval** run has `principal_id = None` (`17 §7`). Each is handled explicitly in
§4/§8 — a `None` subject never silently resolves to someone else's connection.

> ⚠️ **Amendment to `01` LLD §2 / `00 §4`.** Add `RunContext.caller_subject()` (above). It is a pure derivation
> over existing fields (`principal`/`principal_id`), so no new state. **(Now folded into `01` LLD §2 as a
> `RunContext` method — 2026-10-25 reconciliation.)**

---

## 4. Invoke-time resolution — the refined MCP tool node (fills `17 §5`)

Resolved in the MCP tool node (`services/runtime/graph/nodes.py`, `03` LLD §4) **after** the `can_use`
visibility gate (`09 §5`) — visibility is unchanged; this adds the downstream-identity step. It returns either an
MCP client or a `ToolUnavailable` result (a category-B outcome the node turns into a tool result, `03` LLD §5):

```python
import hashlib


def _subject_hash(subject: str) -> str:
    return hashlib.sha256(subject.encode()).hexdigest()      # the user-DEK ref (09 LLD §3.1)


async def resolve_mcp_client(binding, server, ctx, *, vault, db):
    # auth_mode + mcp_scopes are read FROM THE FROZEN SNAPSHOT BINDING (ToolBindingSnap, 01 §1/§4.2), pinned at
    # publish — NEVER off the live `server` row, whose auth_mode/scopes may have drifted since (immutability, §7).
    if binding.auth_mode != "per_user":
        headers = await shared_cred(server, ctx, vault=vault, db=db)   # §2 — the shared service identity
        return mcp_client(server, auth=headers)                        # runs in the gVisor sandbox (11 §1)

    subject = ctx.caller_subject()                                     # §3
    if subject is None:
        return _eval_or_exclude(server, ctx)                          # eval / subject-less caller (§7, §8)
    conn = await get_user_connection(db, server.id, subject)          # RLS tenant-scoped (09 LLD §2); unique(server,subject)
    if conn is None or conn.status != "connected":
        return _connect_required(binding, server, ctx)               # cat. B → in-chat link w/ binding.mcp_scopes (§5, §8)
    token = await _access_token(conn, vault)                          # refresh grant; may flip status→expired
    if token is None:
        return _connect_required(binding, server, ctx)               # refresh refused → re-consent (§8), NOT a silent downgrade
    return mcp_client(server, auth={"Authorization": f"Bearer {token}"})   # acts AS the user (§1 OBO)


async def _access_token(conn, vault) -> str | None:
    """Exchange the stored refresh token for a fresh access token (standard OAuth refresh_token grant). The
    refresh token is per-USER DEK scoped (09 LLD §3). Rotating refresh tokens are re-vaulted atomically (17 §8)."""
    sh = _subject_hash(conn.user_subject)
    refresh = (await vault.decrypt("user", sh, conn.vault_path)).decode()   # per-user DEK; memory-only (09 §9)
    grant = await oauth_refresh(conn.oauth_token_url, refresh)              # provider refresh grant
    if grant is None:                                                       # refused / expired / revoked upstream
        await _set_status(conn, "expired")
        return None
    if grant.new_refresh is not None:                                       # rotating refresh → re-vault same path (17 §8)
        conn.vault_path = await vault.encrypt("user", sh, grant.new_refresh.encode())
    conn.last_used_at = _now()
    conn.expires_at = grant.access_expires_at
    return grant.access_token
```

- **`auth_mode` + `mcp_scopes` come from the snapshot binding, not the live server.** `resolve_mcp_client`
  branches on `binding.auth_mode`, and the connect affordance requests `binding.mcp_scopes` (§5) — both pinned on
  `ToolBindingSnap` at publish (`01 §1/§4.2`). Flipping `auth_mode` or editing scopes on the live `mcp_servers`
  row changes **nothing** at runtime until a re-publish re-pins the snapshot (the immutable-snapshot invariant,
  §7); because both are DRIFT_TRIGGERS, that re-point is eval-gated (`05 §5/§6`).
- **The per-user refresh token never leaves Vault in clear** beyond this call — decrypt JIT, use, discard
  (`09 §9`, `17 §8`). Only the **refresh** token is persisted (as a `VaultEnvelope`); access tokens are ephemeral.
- **`conn.vault_path` holds the `VaultEnvelope` dict**, not a bare pointer — reconciling the parent `17 §3`
  comment ("Vault Transit pointer") with the envelope scheme (`09` LLD §3.2: the `{wrapped_dek,nonce,ct,key}` is
  what's stored in "the `vault_path`/credential column"). §7 notes the column should be `jsonb` accordingly.
- **No silent downgrade.** A missing/dead connection never falls back to `shared_service` (that would defeat the
  per-user audit goal, `17 §6`); it returns connect-required. The opt-in fallback (`17 §6 ⚠️`) is off by default,
  emits a `substitution` span + Run flag when enabled, and is barred for destructive tools.

---

## 5. The connect flow — signed state + single-use nonce (fills `17 §4`)

Two routes + a signed, single-use `state` that binds the OAuth callback to the right identity (the
anti-mix-up/CSRF boundary, `17 §4`):

```python
import hmac, hashlib, json, base64, time, secrets as rand

_STATE_TTL_S = 600                                   # 10 min — long enough to complete a browser OAuth, short enough to limit replay


def sign_connect_state(payload: dict, *, key: bytes) -> str:
    """HMAC-SHA256 over {user_subject, workspace_id, mcp_server_id, nonce, exp}. key = a platform signing secret
    (platform DEK scope, like the IdP creds — 18 §7). Opaque to the client."""
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


async def verify_connect_state(state: str, *, key: bytes, redis) -> dict:
    body, _, sig = state.partition(".")
    if not hmac.compare_digest(sig, hmac.new(key, body.encode(), hashlib.sha256).hexdigest()):
        raise AccessDenied("invalid connect state")                       # 403 (00 §1)
    payload = json.loads(base64.urlsafe_b64decode(body))
    if payload["exp"] < time.time():
        raise AccessDenied("connect state expired")
    # SINGLE-USE: SET NX burns the nonce; a replayed callback loses the race → rejected (same primitive as 00 §3).
    if not await redis.set(f"mcp:nonce:{payload['nonce']}", "1", nx=True, ex=_STATE_TTL_S):
        raise AccessDenied("connect state already used")
    return payload
```

```python
# GET /api/v1/mcp-servers/:id/connect  (console inline / channel reply-with-link, 17 §4)
# `scopes` = the snapshot-pinned binding.mcp_scopes (ToolBindingSnap, 01 §1/§4.2), threaded from the connect
# affordance (§4 _connect_required) — NOT a live _requested_scopes(server_id) lookup, so consent matches the
# PUBLISHED config and a live scope edit takes effect only after a re-publish re-pins the snapshot (§7).
async def connect_start(server_id, ctx, *, redis, key, scopes):
    subject = ctx.caller_subject()
    state = sign_connect_state(
        {"user_subject": subject, "workspace_id": ctx.workspace_id, "mcp_server_id": server_id,
         "nonce": rand.token_urlsafe(16), "exp": time.time() + _STATE_TTL_S}, key=key)
    return _oauth_authorize_url(server_id, state=state, scopes=scopes)   # 302 to the provider

# GET /api/v1/mcp-connect/callback?code=…&state=…  (out-of-band web OAuth, 17 §4)
async def connect_callback(code, state, *, db, vault, redis, key):
    p = await verify_connect_state(state, key=key, redis=redis)            # binds identity; burns the nonce
    grant = await oauth_code_exchange(p["mcp_server_id"], code)            # code → {refresh_token, scopes, exp}
    sh = _subject_hash(p["user_subject"])
    env = await vault.encrypt("user", sh, grant.refresh_token.encode())    # per-user DEK (09 LLD §3)
    await upsert_user_connection(db, workspace_id=p["workspace_id"], mcp_server_id=p["mcp_server_id"],
                                 user_subject=p["user_subject"], vault_path=env, scopes=grant.scopes,
                                 expires_at=grant.expires_at)              # idempotent on unique(server, subject)
```

- **Callback idempotency.** The upsert rides `unique (mcp_server_id, user_subject)` (`17 §3`) `ON CONFLICT DO
  UPDATE` → a re-delivered callback or a second browser tab converges on one `connected` row, not a duplicate.
  The nonce single-use (above) makes a **replayed** callback a hard `403`, so idempotency and anti-replay are
  separate controls (one tolerates retries of the *same* grant, the other rejects a *reused* state).
- **Connect-once, reuse everywhere — *iff the subject namespace matches across planes*.** The row is keyed
  `(workspace, user_subject, mcp_server)` (`17 §3`), so a later session / another channel / the console skip the
  connect step **only when `ctx.caller_subject()` yields the same value in every plane**. Concretely: the channel
  path resolves its subject via `idp.resolve_subject(email)` (`12 §4`, `18` LLD §4), and that MUST return the
  **same IdP `sub` namespace** the console gets as `principal.subject` (the OIDC `sub` of an interactive login).
  `18` LLD §4 pins `resolve_subject` to exactly that `sub` namespace (not the email) for this reason — if it
  returned anything else, the channel would mint a *different* `user_subject`, key a *separate* connection row, and
  the reuse would silently break (the user re-connects per plane).

---

## 6. Trust, allowlist & per-workspace isolation (assembles `14 §5` + `09`)

MCP governance is **open registration** (`14 §5`): any builder+ registers a workspace-scoped `McpServer`; there
is no vetted catalog. Trust comes from three existing machines + the identity layer, not a gatekeeper:

- **Per-resource `access_policy`** (`09 §5`) — who may `can_use`/`can_manage` the server; the visibility gate runs
  **before** §4's identity step (unchanged).
- **Per-server `config.egress` deny-by-default allow-list** (`09 §10`, `01 §4.6`) — enforced at the gVisor
  **sandbox boundary** (`11 §1`); the per-user delegated token is bounded by the same egress rules, so acting as
  the user cannot widen where the tool may reach.
- **Scopes bound the grant** — a `per_user` server declares the OAuth scopes it requests (`oauthScopes`,
  `data.ts`); the delegated token can do no more.
- **Audit** — `mcp.register`/`mcp.discover`/`tool.bind` are audited (`09 §12`); a `per_user` connect/revoke is a
  per-user action on the connection row.

**Per-workspace × per-user isolation** is enforced *twice* for a `per_user` connection: the row is tenant-scoped
by **RLS** (`mcp_user_connections` is `WorkspaceScoped`, §7), and the refresh token is **per-user DEK** scoped —
so user U cannot decrypt user V's grant *and* tenant A cannot decrypt tenant B's (the three-part `09 §8` secrets
test, part c). This **reuses the `18`-derived subject** as the connection key (§3), so MCP identity rides the
same IdP spine as the console/channel/token planes — no parallel identity store. **Cross-plane reuse is therefore
conditional on subject-namespace equality:** "connect once (console), reuse everywhere (channel)" holds iff
`ctx.caller_subject()` is the **same IdP `sub`** in both planes — the channel's `idp.resolve_subject(email)`
(`12 §4`) must return the identical `sub` the console carries as `principal.subject` (`18` LLD §4 pins this). Where
an IdP's `sub` is not reproducible from an email lookup (§8, `18` LLD §4), the planes do **not** transparently
share a connection and the user re-connects per plane.

---

## 7. Data-model deltas (not yet in `01`)

The parent `17 §3` holds the DDL; this reconciles it with the `01` LLD contracts and flags what must land when
`17` is locked into `01`.

- **`mcp_auth_mode` enum + `mcp_servers.auth_mode`** (`17 §3`) — matches `data.ts McpServer.authMode`
  (`shared_service|per_user`). The **same axis exists on HTTP tools** (`data.ts HttpToolConfig.authMode` +
  `oauth-app.ts`); the connect/connection mechanism here is shared — an HTTP `per_user` tool uses the identical
  `mcp_user_connections`-shaped flow (out of scope for the parent's title, noted for parity).
- **`mcp_user_connections`** (`17 §3`) is `WorkspaceScoped` → **add it to the RLS generation loop**
  (`09` LLD §2.2 `WORKSPACE_SCOPED` list) so a new tenant table can't ship without a policy (the `09` LLD §2.4 CI
  check enforces this). The **`vault_path` column should be `jsonb`** (it stores the `VaultEnvelope` dict, §4 /
  `09` LLD §3.2), not plain `text` as the parent sketch types it — a minor reconciliation.
- **Two secrets, two DEK scopes** (§2): the per-user **refresh token** → per-user DEK (`09` LLD §3.1
  `user-<subject_hash>`); the workspace's OAuth **app client secret** (for `per_user` servers) → per-workspace
  DEK. Both are `VaultEnvelope`s; neither stores plaintext.

> ⚠️ **`auth_mode` + `mcp_scopes` are now carried on the frozen `ToolBindingSnap` (pinned at publish).** They are
> fields on `ToolBindingSnap` and are populated by `to_snapshot()` (`01` LLD §1/§4.2), so the runtime reads
> `binding.auth_mode` / `binding.mcp_scopes` **off the snapshot** (§4 `resolve_mcp_client`, §8
> `preflight_per_user_mcp`, §5 connect scopes) — **never** off the live `mcp_servers` row. Consequence (the
> **immutable-snapshot invariant**, `03`/`00 §4`): flipping `auth_mode` or editing scopes on the live
> `mcp_servers` row **changes nothing at runtime** until a re-publish re-pins the snapshot — matching the
> platform's "editing an agent changes nothing live until an eval-gated re-point" rule. Both fields are
> **DRIFT_TRIGGERS**: they are folded into `compute_snapshot_hash` (the `tool_bindings` entry adds `auth_mode` +
> `mcp_scopes`, `None` for non-MCP bindings, `01` LLD §4.2/§4.3) **and** listed in the DRIFT_TRIGGERS set
> (`08` LLD §7), so a `shared_service → per_user` flip or a scope change busts the verdict cache and re-arms the
> publish gate (`05 §5/§6`). **(Folded into `01` LLD §4.2/§4.3 + `ToolBindingSnap` — 2026-10-25 reconciliation.)**

The `per_user` **token** is runtime state, never in `snapshot_hash` — so "reproducible" means *reproducible given
the same identity* (`17 §7`); the *config* (`auth_mode`) is pinned, the *credential* is not.

---

## 8. Error taxonomy & edge cases

Mapping onto `00 §1`'s three categories — the subtle part is that an MCP auth problem surfaces **differently
before vs during** a streaming run:

| Condition | Plane | Category (00 §1) | Handling |
|---|---|---|---|
| no `per_user` connection, **interactive** caller | console / channel-federated | **B** (caught in node, `03 §5`) | `ToolUnavailable(reason="mcp.connect_required", connect_url=…)` → inline prompt / in-chat link; the run continues (the model may route around it) |
| no `per_user` connection, **non-interactive** caller | api token / service | **A** (pre-stream preflight) | `403 mcp.connect_required` **before any spend** — there is no interactive connect path (`17 §6`) |
| refresh token expired/revoked **mid-run** | any | **B** | `_access_token` returns `None` → `status='expired'` → connect-required result; **never** a silent `shared_service` fallback (`17 §6`), never a 5xx |
| `shared_service` cred decrypt / MCP tool error | any | **B** | per-tool catch (`03 §5`) → error *result* to the model; a run outcome, not an HTTP error |
| `per_user` tool in an **eval** run | eval | — | `principal_id = None`: resolve to a designated workspace **eval service connection**, else **exclude the tool path** from scoring + flag the suite low-confidence (`17 §7`) |
| opt-in `per_user → shared_service` fallback (if enabled) | api token | B (flagged) | emits a `substitution` span + Run flag (`02 §5`); **barred for destructive tools**; off by default (`17 §6 ⚠️`) |

The preflight is possible because **tool bindings are known from the snapshot before any model call** — so
`invoke()` (or the `08` invoke router) can detect "a non-interactive caller binds a `per_user` MCP with no
connection" and fail fast, honoring the parent `17 §6`'s "`403 mcp.connect_required`" while respecting the
streaming rule that **once SSE starts, failures are `stop_reason`s, not 4xx** (`00 §1` cat. C, `03` LLD §5):

```python
class McpConnectRequired(AccessDenied):      # 00 §1: AccessDenied family (403); distinct code
    code = "mcp.connect_required"


async def preflight_per_user_mcp(snapshot, ctx, *, db) -> None:
    """Pre-stream guard for NON-interactive callers. Interactive callers skip this — they get the in-node
    connect affordance (table). Keeps a clean 403 off the streaming path."""
    if _interactive(ctx):                    # console principal or channel-federated user (17 §6)
        return
    subject = ctx.caller_subject()
    for b in snapshot.tool_bindings:
        if b.kind == "mcp" and b.auth_mode == "per_user":       # auth_mode read off the FROZEN snapshot binding (§7)
            if subject is None or await get_user_connection(db, _server(b).id, subject) is None:
                raise McpConnectRequired("connect your account before invoking this agent",
                                         detail={"mcp_server": _server(b).name})
```

> ⚠️ **Amendment to `08 §1` error table.** Add a row: `missing per-user MCP connection (non-interactive caller)
> → 403 mcp.connect_required`, class `McpConnectRequired(AccessDenied)`. It is the only MCP-identity outcome the
> `08` table doesn't cover. **(Now folded into parent `08 §1` as the `403 mcp.connect_required` row — 2026-10-25
> reconciliation; the `per_user` feature itself stays P2+.)**

**Other edge cases:** replayed OAuth callback → nonce burned → `403` (§5); concurrent connect (two tabs) →
idempotent `ON CONFLICT` upsert (§5); rotating refresh token → re-vaulted at the same `vault_path` atomically
(§4, `17 §8`); upstream revoke propagation (`17 §8` `[OPEN — P2.a]`) remains unspecified here — we stop *our* use
on `status='revoked'`, but calling the provider's revoke endpoint is best-effort and not yet designed.

---

## 9. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the `00`–`09` LLDs); the HMAC
   state, the refresh grant, and the tool-node body (import-stubbed for `oauth_*`, `mcp_client`, `redis`).
2. **Cross-ref** — against `17` read in full (`auth_mode` §2, `mcp_user_connections` §3, connect flow §4,
   invoke resolution §5, caller modes §6, eval caveat §7, token lifecycle §8); `09 §5` (`can_use` visibility
   gate), `09 §6` (`resolve_caller` token subject), `09 §8` (3-part secrets isolation), `09 §9`/`09` LLD §3 (DEK
   envelope + the per-user/per-workspace scopes), `09 §10`/`11 §1` (sandbox + egress); `01` LLD §2 (`RunContext`
   — the `caller_subject()` gap), §1/§4.2 (`ToolBindingSnap` + `compute_snapshot_hash` now carry `auth_mode` +
   `mcp_scopes`; §4/§8 read them off the snapshot binding), §3 (`AccessPolicy`);
   `03` LLD §4/§5 (MCP tool node + per-tool cat-B error model); `00` LLD §1 (the 3-category taxonomy), §3
   (idempotency/`SET NX` nonce primitive); `12 §4` (channel federation subject, Entra OBO); `14 §5`
   (open-registration); `18` LLD (adapter subject + `exchange_obo` for P2.c); frontend `mcp.ts`
   (`McpAuth`), `mcp-credentials.ts`/`secrets.ts` (`CredentialRef`), `runtime-access.ts`
   (`connectTools`/`isPerUserMcp`/interactive-caller rule), `data.ts` (`McpServer.authMode`/`oauthScopes`,
   `HttpToolConfig.authMode`).
3. **On-behalf-of pinned** — the delegated-grant decision (§1) is stated as locked, with the per-mode
   downstream-identity contract; token-exchange OBO scoped to P2.c (consistent with `17 §9` phasing + `12 §4`).
4. **Contradictions surfaced, not papered over** — (a) `ctx.caller_subject()` had no definition → added as a
   `RunContext` method + amendment (§3); (b) `auth_mode` + `mcp_scopes` are now carried on the frozen
   `ToolBindingSnap` (`01` LLD §1/§4.2) and the runtime reads them off the **snapshot binding** (§4/§8), not the
   live `mcp_servers` row — the immutable-snapshot invariant; both remain DRIFT_TRIGGERS (hash + set, §7); (c) the
   `mcp.connect_required` "403" vs a streaming run → resolved via the preflight/cat-B split + an `08 §1` row flag
   (§8); (d) cross-plane "connect once, reuse everywhere" (§5/§6) is explicitly conditioned on subject-namespace
   equality — `idp.resolve_subject(email)` returning the same IdP `sub` as `principal.subject` (`18` LLD §4). None
   presented as already adopted — `17` is explicitly P2+/not-yet-in-`01`/`08`.

→ Sibling: [`18-identity-adapters.lld.md`](18-identity-adapters.lld.md) (the IdP adapter whose subject keys the
`mcp_user_connections` and whose `exchange_obo` is the P2.c OBO path).
