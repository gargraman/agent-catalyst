# 09 (LLD) — Security, Auth & Multi-Tenancy

> **⚠️ Secret custody — as built (feature 014, 2026-11-11):** provider-key custody now runs through a pluggable secrets backend (**AWS Secrets Manager** / LocalStack / `local`), **not** HashiCorp Vault Transit. Every Vault / `transit/keys/*` / DEK / AppRole detail in this LLD (incl. the §3 envelope design) is the **superseded design baseline** — the provider-keys row is BUILT on the new backend; the other secret classes are the baseline for the still-unbuilt cred families, to be re-reconciled when each is built. See `docs/scope.md` (2026-11-11) and [`../09-security-tenancy.md §9`](../09-security-tenancy.md).

> **Parent:** [`../09-security-tenancy.md`](../09-security-tenancy.md) (the three auth planes, OIDC/claim
> sourcing, the workspace-scope guard, RBAC, per-resource access policy, caller tokens, the RLS backstop, the
> Vault-Transit envelope + key custody, the threat model, retention, audit).
> The parent fully specifies the **model and the decisions**; it leaves the **load-bearing mechanisms other
> LLDs already depend on** at HLD altitude: *how* the tenant GUC is actually set on a session (so `01 §6`'s
> repository pattern is RLS-safe), the **concrete RLS policy DDL** (the parent shows one `CREATE POLICY` and
> says "repeat for every table"), the **per-user DEK envelope** (C4 — named but not built), and the **org-plane
> DB-role / `EXECUTE`-revocation boundary** (C4 — named in §7/§8 but no DDL). This doc makes those buildable.

## What this adds over parent `09`

| Gap in `09` | Filled here |
|---|---|
| `tenancy.py` sets `SET LOCAL app.workspace_id` (§3) but the **session plumbing** (txn-scoped `AsyncSession`, the `set_config` form, the worker path) is prose; `01 §6` repo pattern *depends* on this GUC being set | §1 — the dependency chain, the buildable GUC set (the `set_config(..., true)` correction), the worker-job wrapper (C4) |
| RLS shows **one** `CREATE POLICY` + "repeat for every `WorkspaceScoped` table" (`01 §6`); no `FORCE`, no `WITH CHECK`, no unset-GUC guard, no generation loop | §2 — the per-table DDL template (enable+force, `USING`/`WITH CHECK`), the fail-closed unset guard, the one-migration loop, the CI assertion |
| Per-user DEK (C4, §9.1 / `17 §3`) **named** — "per-USER DEK" — but the envelope scheme, Vault wiring, wrap/unwrap, rotation are undefined | §3 — the three DEK scopes, the datakey envelope, buildable `KeyVault`, key naming, rotation/rewrap |
| Org-plane isolation (C4, §7/§8) **named** — "`SECURITY DEFINER` … `EXECUTE`-granted only to `platform_report`" — no function DDL, no grant/revoke, no separate connection | §4 — the `SECURITY DEFINER` function template, the `REVOKE`/`GRANT` boundary, the dedicated engine, the DB-level isolation test |
| Immediate revocation (`09 §2.1`/`§6`, 2026-10-25) names a **transient Redis deny-set** + admin `force-logout` but not the keys, the fail-closed check, the TTL, or the test | §5 — the deny-set key spaces + TTL, the console/caller check points, fail-closed-on-outage, the admin write path, the isolation-suite case |

Python 3.12, `from __future__ import annotations`, SQLAlchemy **`AsyncSession`** throughout (session model pinned
in [`00-conventions.lld.md §4`](00-conventions.lld.md)). SQL is PostgreSQL 16. Snippets compile (§5). `Principal`,
`WorkspaceCtx`, `can_use`/`can_manage`, `resolve_caller`, `VaultTransit`: **see `09` — not restated.**

---

## 1. The tenancy dependency — setting `app.workspace_id` (the GUC the RLS backstop needs)

`01 §6`'s repository pattern is RLS-safe **only because** the request's DB session already carries the tenant
GUC — repos deliberately never add a manual `WHERE workspace_id` (`01 §6`). This is the mechanism that makes
that true. It is load-bearing for **every** tenant query, the `03` invoke path's run/span writes, and `04`'s
`resolve_kb_access_policy` live read (`04` LLD §2.1).

### 1.1 The dependency chain (console/API request)

One `AsyncSession` per request, opened in a transaction so `SET LOCAL` is scoped to it and cannot bleed across
pooled checkouts (`09 §7`, `00-conventions.lld.md §4.1`):

```python
# services/api/deps.py
from __future__ import annotations
from collections.abc import AsyncIterator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db() -> AsyncIterator[AsyncSession]:
    """One AsyncSession per request, in a single transaction (00-conventions §4.1). A transaction-scoped pool
    mode (PgBouncer `transaction`) + this `async with session.begin()` guarantee SET LOCAL resets at commit."""
    async with SessionLocal() as session:          # async_sessionmaker(bind=async_engine, expire_on_commit=False)
        async with session.begin():                # the request transaction — SET LOCAL lives and dies here
            yield session


async def set_tenant_guc(db: AsyncSession, workspace_id: str) -> None:
    """Pin the tenant on THIS connection for RLS (§2). Uses set_config(..., is_local=true) rather than a
    literal `SET LOCAL app.workspace_id = :w`: `SET` does NOT accept bind parameters, so the parent's
    `text("SET LOCAL app.workspace_id = :w")` would fail to bind — set_config() takes a real parameter and
    `true` makes it transaction-local (resets on commit, exactly like SET LOCAL)."""
    await db.execute(text("select set_config('app.workspace_id', :w, true)"), {"w": workspace_id})
```

`get_workspace_ctx` (`09 §3`) resolves + validates the workspace, asserts the role claim, then calls
`set_tenant_guc(db, ws_id)` **before any tenant query runs**. After that, `01 §6` repos issue plain
`await db.get(Model, id)` / `await db.scalar(select(...))` and RLS (§2) filters the tenant automatically — a
cross-tenant id returns `None`, surfaced as `404` (`09 §8`, never a `403` that would confirm existence).

> ⚠️ **Amendment to `09 §3`:** the parent's `await db.execute(text("SET LOCAL app.workspace_id = :w"), {"w": ws_id})`
> binds a parameter into a `SET` statement, which PostgreSQL rejects. The buildable form is
> `select set_config('app.workspace_id', :w, true)` (shown above). Same semantics (txn-local), correct binding.

### 1.2 The worker path (C4 — same GUC, per job)

The `worker` (`00 §2`) runs eval/ingestion/scan jobs outside an HTTP request, so it must set the GUC itself at
job start and **reset at job end** so the next job on a reused connection can't read the previous tenant
(`09 §8` "worker GUC scoping (C4)"):

```python
# services/worker/tenancy.py
from contextlib import asynccontextmanager


@asynccontextmanager
async def job_tenant(db: AsyncSession, workspace_id: str):
    """Scope a worker job to its tenant. One transaction per job → SET LOCAL resets on commit/rollback, so the
    GUC never leaks to the next job on a pooled connection (the §8 'worker GUC scoping' isolation test)."""
    async with db.begin():
        await set_tenant_guc(db, workspace_id)
        yield db
    # on exit the transaction ends → app.workspace_id is unset again; the next job must set its own.
```

Both callers of `runtime.invoke()` that run in the worker (eval, channel — `00 §1`) resolve the job's tenant
into this scope first, and build the `RunContext.principal` (`03` LLD §2 / `01` LLD §2) the invoke path then
feeds to `can_use` at the KB-grain check (`04` LLD §2.1) and the per-resource access check (`09 §5`).

### 1.3 Why the GUC is the backstop's hinge

The guard (`09 §3`) is the **primary** control and gives clean `403`s; RLS (§2) is the backstop that turns a
forgotten predicate into **zero rows** instead of a cross-tenant leak (`09 §7`). RLS does nothing unless
`app.workspace_id` is set — so §1.1/§1.2 are not optional plumbing, they are the precondition that makes the
`01 §6` "repos never filter by workspace" rule safe. If the GUC is **unset**, the §2 policy fails **closed**
(no rows), not open — see §2.3.

---

## 2. RLS policy DDL template (the one migration loop)

`01 §6` shows a single `agents` policy and says "repeat for every `WorkspaceScoped` table (generated in one
migration loop)." This is that template, with the three things the one-liner omits: **`FORCE`**, a
**`WITH CHECK`** (so a tenant can't *insert/update* a row into another tenant), and the **unset-GUC fail-closed**
guard.

### 2.1 Per-table policy

```sql
-- For EVERY WorkspaceScoped table (01 §6). Shown for `agents`; the loop (§2.2) emits this for each.
alter table agents enable row level security;
alter table agents force  row level security;   -- owner is NOT exempt (defense in depth; app role isn't owner anyway)

create policy agents_tenant on agents
  using      (workspace_id = current_setting('app.workspace_id', true)::uuid)   -- rows VISIBLE to this tenant
  with check (workspace_id = current_setting('app.workspace_id', true)::uuid);  -- rows WRITABLE only for this tenant
```

- **`USING`** gates `SELECT`/`UPDATE`/`DELETE` visibility; **`WITH CHECK`** gates the post-image of
  `INSERT`/`UPDATE` — without it a caller could write a row stamped with *another* `workspace_id`. Both bind to
  the same GUC, so read and write are symmetric.
- **`current_setting('app.workspace_id', true)`** — the `true` (`missing_ok`) means an **unset** GUC returns
  `NULL` instead of erroring; `NULL::uuid` makes the predicate `workspace_id = NULL` → **no rows / no writes**
  (fail closed). A query that forgot to set the tenant gets nothing, never everything (§2.3).
- **`FORCE`** so the policy applies even to a table owner; combined with `09 §7`'s rule that the **app role is
  not the owner and has no `BYPASSRLS`**, there is no role on the request path that sees across tenants.

### 2.2 The generation loop (one migration)

```python
# migrations/versions/xxxx_rls.py — Alembic. The loop is DERIVED from the ORM WorkspaceScoped mixin registry at
# migration time (01 §5/§6) — the list IS the registry, not a hand-kept copy, so a new WorkspaceScoped table
# CANNOT be forgotten. The §2.4 CI check is the backstop (asserts deployed policies == registry).
WORKSPACE_SCOPED = [m.__tablename__ for m in workspace_scoped_models()]   # single source of truth (01 §5/§6)

# As of this migration the registry resolves to the tables below — every one carries `workspace_id` in the
# canonical 01 DDL, so each gets the standard §2.1 column policy. Listed for the reader ONLY; the code above does
# not hand-maintain this (that is the whole point — enumerating it here would reintroduce the drift §2.4 guards):
#   agents, agent_versions, retrieval_bindings, tool_bindings, skill_bindings,
#   knowledge_bases, documents, chunks, data_sources,
#   eval_suites, eval_cases, eval_runs, eval_results, eval_baselines,
#   runs, spans, api_tokens, jobs,
#   notifications, notification_deliveries, notification_prefs,
#   agent_signatures, skills, skill_resources, subagent_configs,
#   provider_keys, tools, tool_approvals, mcp_servers, mcp_tools, mcp_user_connections,   # mcp_user_connections: per-(workspace,user) MCP grants (17 §3)
#   published_endpoints, spend_limits, concurrency_limits,
#   channels, channel_agents, channel_events,                                             # channel tables — WorkspaceScoped w/ workspace_id in canonical 01 (12)
#   audit_logs                                                                            # per-workspace rows; the NULL-ws org rows (§4) are invisible to the policy

def upgrade() -> None:
    for t in WORKSPACE_SCOPED:
        op.execute(f"alter table {t} enable row level security")
        op.execute(f"alter table {t} force  row level security")
        op.execute(
            f"create policy {t}_tenant on {t} "
            f"using (workspace_id = current_setting('app.workspace_id', true)::uuid) "
            f"with check (workspace_id = current_setting('app.workspace_id', true)::uuid)"
        )
```

`audit_logs` is in the loop for its per-workspace rows; its `workspace_id = NULL` org-level rows (`09 §12`) are
invisible to this policy and reachable only through the elevated path (§4) — exactly the intended carve-out.

### 2.3 The unset-GUC fail-closed property (why `missing_ok = true`)

The subtle correctness point: if `app.workspace_id` is never set (a bug, or a connection that skipped
`get_workspace_ctx`), `current_setting('app.workspace_id', false)` would **raise**, turning every query on that
connection into a 500 — loud, but it also means the *failure mode under a plumbing bug is an error, not a
silent leak*. We choose `true` (return `NULL` → no rows) so the failure mode is **fail-closed and quiet at the
data layer** while the guard (`09 §3`) still raises the clean `403`/`400` at the app layer. Net: a missing GUC
yields no data via two independent controls, never another tenant's rows.

### 2.4 CI assertion (the §7 check, made concrete)

```python
# tests/isolation/test_rls_coverage.py — a gate (09 §8). Two assertions:
def test_every_workspace_scoped_table_has_a_policy(db_meta, pg):
    scoped = {t.__tablename__ for t in workspace_scoped_models()}      # from the ORM mixin registry (01 §5/§6)
    with_policy = pg.query("select tablename from pg_policies where policyname like '%_tenant'")
    assert scoped <= set(with_policy), f"WorkspaceScoped tables missing an RLS policy: {scoped - set(with_policy)}"

def test_unset_guc_returns_zero_rows(pg_app_role):
    pg_app_role.execute("reset app.workspace_id")                      # simulate a forgotten set_tenant_guc
    assert pg_app_role.query("select count(*) from agents")[0] == 0    # fail closed, not a leak
```

This is the mechanism the `01 §6` repository pattern **relies on**: because RLS + the GUC are proven present for
every tenant table, a repo issuing a bare `await db.get(Model, id)` is safe by construction.

---

## 3. Per-user DEK envelope (C4)

`09 §9` builds the base Vault-Transit envelope with a **per-workspace** DEK; `09 §9.1` (ratified 2026-10-22)
splits custody into **three scopes** but leaves the **per-user** scheme — the one new in C4 — as a named bullet.
This specifies it, consistent with the base scheme.

### 3.1 The three DEK scopes (one envelope, scope-derived key names)

The custody split is **three scopes** — `platform`, `per-workspace`, `per-user` (`09 §9.1`). The `platform`
scope is not a single key: it holds **named sub-keys** (`platform-<ref>`), one per platform-held secret class,
each its own Transit key for blast-radius isolation.

| Secret class | DEK scope | Vault Transit key name | Why |
|---|---|---|---|
| **Provider keys** | **platform** (`pool` sub-key) | `transit/keys/platform-pool` | custody spine — the pool key is never exposed to any workspace (`09 §9.1`) |
| **IdP / Graph API creds** (`18 §7.3`) | **platform** (`idp` sub-key) | `transit/keys/platform-idp` | deployment-wide IdP/Graph secrets; a key separate from `platform-pool` for blast-radius isolation (`18 §7.3`) |
| **Connect signing key** | **platform** (`connect-signing` sub-key) | `transit/keys/platform-connect-signing` | platform-held connect-flow signing material; its own sub-key, never per-workspace |
| Tool / connector / channel creds | **per-workspace** | `transit/keys/ws-<workspace_id>` | tenant-isolated; the base scheme (`09 §9`) |
| **Per-user MCP creds** | **per-user** (C4) | `transit/keys/user-<subject_hash>` | least-privilege: a user's connected-account token is readable only as that user (`17 §3`) |

`subject_hash = sha256(oidc_subject)` (hex) so the Vault key name carries no raw PII and is stable per user.
A per-user secret is additionally stored under its owning `workspace_id` (RLS, §2) — so decrypt is gated
**twice**: the row is tenant-scoped by RLS, and the DEK is user-scoped by the key name (`09 §8`: "cannot be
decrypted across tenants *or* across users").

### 3.2 The envelope — Transit `datakey`, not raw root encryption

Secrets can be large (headers bundles, OAuth token sets), so we use **envelope encryption**: a one-off DEK
encrypts the payload locally (AES-256-GCM); the DEK is **wrapped** by the scope's Transit key; the Transit root
never leaves Vault.

```python
# services/runtime/secrets.py  — refines the 09 §9 KeyVault Protocol for the per-user (and every) scope.
from __future__ import annotations
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM   # AES-256-GCM, authenticated


def _key_name(scope: str, ref: str | None) -> str:
    # scope ∈ {"platform", "workspace", "user"}:
    #   platform  → ref names the sub-key → platform-<ref> ("pool" provider keys | "idp" 18 §7.3 | "connect-signing");
    #               ref=None keeps the legacy provider-key spine (platform-pool)
    #   workspace → ref = workspace_id    → ws-<workspace_id>
    #   user      → ref = subject_hash    → user-<subject_hash>
    return {"platform": f"platform-{ref or 'pool'}", "workspace": f"ws-{ref}", "user": f"user-{ref}"}[scope]


class VaultEnvelope:
    def __init__(self, vault): self.vault = vault      # hvac/async Vault client, AppRole-authed at boot (09 §9)

    async def encrypt(self, scope: str, ref: str | None, plaintext: bytes) -> dict:
        key = _key_name(scope, ref)
        dk = await self.vault.transit_datakey(key)      # POST transit/datakey/plaintext/<key> → {plaintext, ciphertext}
        nonce = os.urandom(12)
        ct = AESGCM(dk["plaintext"]).encrypt(nonce, plaintext, None)
        del dk["plaintext"]                             # never persist the bare DEK; keep only Vault's wrapped form
        return {"wrapped_dek": dk["ciphertext"], "nonce": nonce.hex(), "ct": ct.hex(), "key": key}

    async def decrypt(self, scope: str, ref: str | None, env: dict) -> bytes:
        key = _key_name(scope, ref)
        if env["key"] != key:
            raise PermissionError("DEK scope mismatch")  # a per-user secret can't be unwrapped under another scope/user
        dek = await self.vault.transit_decrypt(key, env["wrapped_dek"])   # POST transit/decrypt/<key> → plaintext DEK (memory only)
        return AESGCM(dek).decrypt(bytes.fromhex(env["nonce"]), bytes.fromhex(env["ct"]), None)
```

- Only `{wrapped_dek, nonce, ct, key}` is persisted (in the `vault_path`/credential column, `01 §4.3/§4.4/§4.6`);
  the **plaintext DEK and the secret exist only in memory for the call lifetime** (`09 §9` just-in-time decrypt,
  `del` after use).
- The `env["key"] != key` check makes a scope/user mismatch a hard failure, so a stolen row for user V cannot be
  decrypted as user U even if the attacker controls the `scope`/`ref` passed in — the stored `key` must match.

### 3.3 Rotation (rewrap, no plaintext)

Transit keys rotate **without** touching ciphertext: `transit/keys/<key>/rotate` bumps the key version; the DEK
wrapper is re-encrypted to the new version via `transit/rewrap/<key>` — the payload AES-GCM blob is untouched.

```python
async def rotate_scope(self, scope: str, ref: str | None, db) -> None:
    """Rotate a DEK scope: bump the Transit key, then rewrap every stored wrapped_dek under it. The secret
    plaintext is NEVER decrypted — rewrap operates on the wrapper only (Vault-side)."""
    key = _key_name(scope, ref)
    await self.vault.transit_rotate(key)
    for row in await _secrets_for_scope(db, scope, ref):          # RLS-scoped (§2) for ws/user scopes
        row.cred["wrapped_dek"] = await self.vault.transit_rewrap(key, row.cred["wrapped_dek"])
    # per-user rotation can be triggered on reconnect / credential refresh (17); provider-pool + per-workspace
    # rotation are an admin/ops action (09 §4 key.manage), audited (provider_key.rotate / source.rotate, §12).
```

This ties back to `09 §9` (the envelope), `09 §9.1` (the three custody scopes), and `17 §3` (the per-user MCP
identity that owns the `user-` scope). The §8 secrets isolation test (part c) exercises exactly this: user U
cannot decrypt user V's `user-` scoped secret, and tenant A cannot decrypt tenant B's `ws-` scoped secret.

---

## 4. Org-plane isolation — separate DB role + `EXECUTE` revocation (C4)

`09 §7` specifies the elevated cross-workspace read (the org governance report, `15`) as a **`SECURITY DEFINER`**
function callable **only** by a dedicated reader role, and `09 §8` asserts a **DB-level** test that the general
app role has **no `EXECUTE`** on it. This is the DDL + the connection boundary that make that real — the control
that means an SQLi/logic bug on the normal app path **cannot** cross tenants, because the normal role physically
cannot invoke the cross-tenant function.

### 4.1 Three DB roles

| Role | Owns | `BYPASSRLS`? | Used by |
|---|---|---|---|
| `ac_migrate` | all DDL (tables, policies, functions) | **yes** — required to overcome the §2 `FORCE` RLS (offline DDL/migration role only; never on the request path) | Alembic only, offline |
| `ac_app` | nothing | **no** | every request/worker connection (§1) — RLS applies, policies force-on |
| `platform_report` | nothing | **no** (crosses tenants only *inside* the `SECURITY DEFINER` fn, which runs as its definer `ac_migrate`) | the **org plane only** (§4.3), a separate engine |

`ac_app` is `09 §7`'s "non-owner app role, no `BYPASSRLS`." `platform_report` is the **only** role granted
`EXECUTE` on the org-report functions; it still has no `BYPASSRLS` of its own — the cross-tenant reach comes
*solely* from the `SECURITY DEFINER` function running as its owner.

> **Why the definer owner must hold `BYPASSRLS`.** Under §2's universal `FORCE` RLS the table owner is **not**
> exempt, so a `SECURITY DEFINER` function owned by `ac_migrate` would be forced under RLS too — and with
> `app.workspace_id` unset on the org-plane connection (§4.3), the policy predicate is `workspace_id = NULL` →
> **zero rows**. The cross-tenant org read would then *silently return nothing*. Granting `BYPASSRLS` to the
> definer **owner** (`ac_migrate`) overrides `FORCE` so the functions actually cross tenants; it is safe because
> `ac_migrate` is offline/DDL-only and never reachable on a request path. `platform_report` deliberately keeps
> **no** `BYPASSRLS` — its only cross-tenant reach is `EXECUTE` on the definer fns. This is the mechanism
> `15` LLD §2.2 depends on.

### 4.2 The `SECURITY DEFINER` function + the grant/revoke boundary

```sql
-- Owned by ac_migrate (the definer). SECURITY DEFINER → the body runs with ac_migrate's rights, so it reads
-- ACROSS tenants (it does NOT set app.workspace_id). This is the single sanctioned exception to §2 (09 §7/§8).
-- Illustrative shape only — the canonical, enriched org_report_agents() (full column set + joins) is defined in
-- 15 LLD §2.3; shown here solely to demonstrate the §4 template (definer + pinned search_path + revoke/grant
-- boundary) that the §4.4 DB-boundary test exercises against the real function.
create or replace function org_report_agents()
  returns table (workspace_id uuid, agents bigint, spend_usd numeric)
  language sql
  security definer
  set search_path = pg_catalog, public          -- pin search_path (SECURITY DEFINER hardening — no hijack)
as $$
  select a.workspace_id, count(*)::bigint, coalesce(sum(r.cost_usd), 0)
  from agents a left join runs r using (workspace_id)
  group by a.workspace_id                        -- METADATA/aggregate only for private-tier (15 §2); no body/content
$$;

-- The boundary: NOBODY executes it by default, then grant ONLY the reader role.
revoke all on function org_report_agents() from public;
revoke all on function org_report_agents() from ac_app;     -- the normal app role explicitly cannot call it
grant  execute on function org_report_agents() to platform_report;
```

- **`security definer` + pinned `search_path`** is the standard hardening — the function reads cross-tenant, but
  a pinned `search_path` stops a search-path-hijack from redefining what it runs.
- **`revoke … from public` + `revoke … from ac_app`** then **`grant … to platform_report`** is the
  `EXECUTE`-revocation boundary: an SQLi or logic bug on an `ac_app` connection *cannot* reach this function —
  the privilege simply isn't there. This is the DB-level control `09 §8` tests.
- It returns **metadata/aggregates only** for `private`-tier resources (`15 §2`); a private *content* read is
  the separate `org.report.content_read` action (`15 §2.3`), never a side effect of the estate read.

### 4.3 The separate connection + the audited call site

The org plane uses a **dedicated engine** authenticated as `platform_report`, never the request `ac_app`
session — so the two privilege domains never share a connection:

```python
# services/api/org/plane.py  — only the admin plane (09 §1, org.report.read) reaches this.
_org_engine = create_async_engine(settings.org_report_dsn)   # DSN authenticates as platform_report (its own creds)

async def read_estate(principal: "Principal") -> list[dict]:
    if not principal.is_admin:                               # app-wide admin claim ONLY (09 §1/§4)
        raise Forbidden("org.report.read")
    async with _org_engine.begin() as conn:                 # platform_report connection — NO app.workspace_id set
        rows = (await conn.execute(text("select * from org_report_agents()"))).mappings().all()
    await _write_org_audit(action="org.report.read", actor=principal.subject_id)   # workspace_id = NULL (09 §12)
    return [dict(r) for r in rows]
```

Every call writes an org-level `audit_logs` row (`workspace_id = NULL`, `09 §12`) — the audit trail for the one
path that crosses tenants.

### 4.4 The DB-level isolation test (the §8 "org-plane DB boundary (C4)")

```python
# tests/isolation/test_org_plane_boundary.py — a gate (09 §8).
def test_app_role_cannot_execute_org_functions(pg_app_role):
    with pytest.raises(InsufficientPrivilege):               # ac_app has no EXECUTE — a hard DB-level denial
        pg_app_role.execute("select * from org_report_agents()")

def test_only_platform_report_can(pg_platform_report):
    assert pg_platform_report.query("select count(*) from org_report_agents()")[0] >= 0
```

Together with §2's RLS proofs and §3's cross-user secret test, this is the DB half of the non-negotiable
tenant-isolation suite (`09 §8`): the normal plane *cannot* cross tenants even with a bug, and the one elevated
path is reachable only by the audited admin plane on its own role.

---

## 5. Immediate revocation — the Redis deny-set (fail-closed)

> Parent `09 §2.1`/`§6` add a **transient Redis deny-set** so an admin `force-logout`/`revoke-user` cuts an
> **active** console session (and caller token) immediately, not at the next token TTL — the one reversal of the
> 2026-10-22 "next-login" bound. This is the buildable mechanism. It is **not** a roster/SCIM: nothing is written
> to Postgres; the set is transient, TTL-bounded, admin-triggered.

### 5.1 The set — keys & TTL

Two Redis key spaces, both checked on every authenticated request:

```python
# services/api/auth/denyset.py
REVOKE_SUBJECT = "revoke:sub:{subject}"     # kills every session/token for a subject
REVOKE_TOKEN   = "revoke:tok:{token_id}"    # kills one caller token (narrower blast radius)

# TTL ≥ the max access-token TTL so a revoked subject can never outlive the entry and silently regain access.
DENYSET_TTL_S = settings.max_access_token_ttl_s          # e.g. 3600 (09 §2.1 "≤1h")

async def revoke_subject(redis, subject: str, *, reason: str) -> None:
    await redis.set(REVOKE_SUBJECT.format(subject=subject), reason, ex=DENYSET_TTL_S)

async def is_revoked(redis, *, subject: str | None, token_id: str | None) -> bool:
    keys = [k for k in (
        REVOKE_SUBJECT.format(subject=subject) if subject else None,
        REVOKE_TOKEN.format(token_id=token_id) if token_id else None,
    ) if k]
    return bool(keys) and await redis.exists(*keys) > 0     # any hit = revoked; Redis outage → see §5.3
```

A subject re-logging in after the TTL expires is fine — the IdP re-asserts the live claim; the deny-set only
needs to cover the *bounded* window in which a still-valid token would otherwise be honored.

### 5.2 The check on the per-request path

Both auth entrypoints consult the set **after** the token verifies but **before** the principal is trusted:

```python
# console/admin — in verify_token (09 §2), after signature + claims validate:
if await is_revoked(redis, subject=principal.subject, token_id=None):
    raise Unauthorized("session revoked")        # 401 → FE redirects to login (frontend-auth-access-plan)

# caller — resolve_caller (09 §6), alongside the existing revoked_at / expires_at checks:
if await is_revoked(redis, subject=tok.scopes.get("subject"), token_id=str(tok.id)):
    raise Unauthorized("revoked")                # unifies the console-session kill with the caller plane
```

Postgres `api_tokens.revoked_at` stays authoritative for caller tokens (§6); the deny-set is the fast, unified
fail-closed layer that *also* covers **console-session subjects**, which have no `api_tokens` row.

### 5.3 Fail-closed on Redis unavailability

Unlike best-effort telemetry (`10 §7` `_safe_emit`), the deny-set is a **security control**: if `is_revoked`
cannot reach Redis, the request is **rejected** (`503`/`401`), never admitted — a revoked user must not slip
through a cache outage. Redis is already a hard request-path dependency (rate-limit §6, idempotency
`00-conventions §3`), so this adds no new failure mode, only a fail-*direction*.

### 5.4 Writing the set — the admin action

`POST /api/v1/org/revoke-user` (`08`, admin plane) calls `revoke_subject` / `revoke_token` and writes an
append-only `audit_logs` row `security.revoke_user {actor, subject|token_id, reason}`. It is a **mutation**, not
the `SECURITY DEFINER` org-read path (§4). No row is written to any user table — there is no user table
(no roster, no SCIM).

### 5.5 Isolation-suite case (the `09 §8` "immediate revocation")

Add to the `09 §8` tenant/isolation suite: mint an active console session for user B; admin `revoke-user(B)`;
assert B's **next** request (same still-valid token) returns `401`; assert a simulated Redis outage during the
check also yields non-2xx (fail-closed); assert an *unrelated* user A is unaffected (the set is subject-scoped,
not global).

---

## 6. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `01`/`00` LLD); SQL blocks
   checked against PostgreSQL 16 syntax (policies, `SECURITY DEFINER`, `set_config`).
2. **Cross-ref** — against `09` read in full: the three planes + `Principal` (`§1/§2`), the guard +
   `SET LOCAL` (`§3`), RBAC (`§4`), access policy + `can_use` (`§5`), caller tokens (`§6`), RLS backstop +
   elevated path (`§7`), the isolation suite incl. C4 items (`§8`), the envelope + custody scopes (`§9/§9.1`),
   audit (`§12`); `01 §6` (RLS loop, repo pattern this doc's §1/§2 underpin); `00-conventions.lld.md §4`
   (AsyncSession, txn-scoped `SET LOCAL`); `03` LLD §2 (`ctx.principal` fed to invoke); `04` LLD §2.1
   (`resolve_kb_access_policy` / the KB-grain `can_use` this GUC makes safe); `15 §2` (org report, private-tier
   metadata-only); `17 §3` (per-user MCP identity owning the `user-` DEK scope).
3. **Dependency closure** — every mechanism another LLD *depends on* is now concrete: `01 §6` repo RLS-safety
   (§1/§2), `03` invoke `ctx.principal` sourcing (§1.2), `04` KB-access live read (§1.2 + §2).
4. **One flagged amendment** — `09 §3`'s `SET LOCAL … = :w` bind bug, corrected to `set_config(..., true)`
   (§1.1), recorded as a callout.

→ LLD backlog: `09` added (10/10 of the originally-9 set + the security LLD). See `README.md`.
