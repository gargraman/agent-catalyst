# 18 — Identity Adapters (IdP-agnostic auth)

> **Why this doc.** The 2026-10-22 claim-based model (`09`, `scope.md`) was written against **Okta** — it
> bakes the `ap-<prefix>-builder` group-**name** convention and `okta.*` API calls into the auth path. Real
> deployments run **Okta, Ping, Azure AD / Entra, AWS (Cognito / IAM Identity Center), or a custom IdP**. We
> must never be dependent on one IdP. This doc defines the **adapter boundary** that keeps the authz core
> vendor-neutral, and records the concessions pluggability forces on the "no roster" model. **Design only —
> nothing built.**

## 1. Principle — the core consumes a normalized contract, never a vendor SDK

The authz core (`get_workspace_ctx` `09 §3`, `can_use`/`can_manage` `09 §5`, RLS `09 §7`, the RBAC `RANK`
`09 §4`) already consumes **only** a normalized `Principal`. That is the seam. Everything that *produces* a
`Principal` — token verification, group→role mapping, subject/group lookup, group-member enumeration — moves
behind an `IdentityProvider` adapter. **No module outside `services/api/identity/adapters/` may import a
vendor SDK (`okta`, `msal`, `boto3`, …).** This is the same pattern the platform already uses for LLMs
(LiteLLM transport + our own capability profiles, `00 §2`): neutral core, thin vendor adapters.

```
                         ┌─────────────────────────── authz core (vendor-neutral) ───────────────────────────┐
  OIDC/SAML token  ──►   │  get_principal → build_principal(adapter)  →  Principal{...}                        │
                         │  get_workspace_ctx · RANK · can_use/can_manage · RLS   (09)                         │
                         └───────────────────────────────────▲───────────────────────────────────────────────┘
                                                              │ normalized contract only
                    ┌─────────────────────────────────────────┴─────────────────────────────────────────────┐
   IdentityProvider │  generic-oidc  │  okta  │  entra (Azure AD)  │  ping  │  cognito  │  custom             │
      (adapters)    └───────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. The adapter interface

```python
# services/api/identity/base.py — the ONLY identity contract the core knows.
class IdentityProvider(Protocol):
    # AuthN — console session
    async def verify_token(self, raw: str) -> Claims: ...          # OIDC verify (iss/aud/exp/jwks) OR SAML assertion
    async def build_principal(self, claims: Claims) -> Principal: ...  # native groups/roles → normalized Principal

    # Channel federation (12) — email→subject→groups for a platform-asserted end user
    async def resolve_subject(self, email: str) -> str | None: ...
    async def groups_for(self, subject: str) -> set[str]: ...

    # Async recipient enumeration (16, Decision 2) — "who is in this role for this workspace"
    async def members_of(self, group_ref: str) -> list[str] | None: ...  # None = IdP exposes no such API

    # Optional, vendor-specific token exchange (Teams/Entra OBO). Default: unsupported.
    async def exchange_obo(self, token: str, audience: str) -> str: ...
```

`Principal` and the caller `CallerCtx` (`09 §1,§6`) are unchanged — that's the point. The adapter fills
`workspace_roles`, `is_admin`, and `teams`; the core never learns how.

## 3. The mapping layer — `workspaces.idp_binding` (replaces `group_prefix`)

The Okta `ap-<prefix>-<role>` **name convention cannot be universal**: Azure/Entra emits group **object
GUIDs** (not names) and truncates large group claims; SAML IdPs send DNs or arbitrary attribute values;
custom IdPs send anything. So role → native-identifier mapping must live in **app-owned config**, per
workspace:

```jsonc
// workspaces.idp_binding (jsonb) — the per-workspace role→native-group map the active adapter resolves.
{
  "viewer":    "<native-ref>",   // okta: "ap-xdr-viewer"  | entra: "<group-guid>" | saml: "<attr-value>"
  "builder":   "<native-ref>",
  "publisher": "<native-ref>",
  "teams":     { "SOC": "<native-ref>", "Detection": "<native-ref>" }
}
// The app-wide admin binding is deployment-level config (one `ap-platform-admin` equivalent), not per-workspace.
```

`group_prefix` survives **only** as sugar the Okta adapter expands into this binding
(`builder → ap-<prefix>-builder`, etc.). Every other adapter writes explicit refs.

> **Honest concession (records a partial reversal of 2026-10-22).** The clean "the IdP token *is* the system
> of record, nothing stored" story only holds when **we control the IdP's group naming** — which we don't
> across vendors. Pluggability therefore re-introduces two app-side things:
> 1. the **`idp_binding` mapping** (small, admin-managed *config* — **not** a per-user roster), and
> 2. an adapter **API client** for `resolve_subject`/`groups_for`/`members_of` (channel + notifier).
>
> What we still **don't** store: per-user workspace membership. AuthZ for a console request still reads the
> role live from the token via the adapter — no roster lookup on the request path.

## 4. Recipient enumeration & the projection cache (closes 16 / Decision 2)

Async notifications (`16`) run in a worker with **no user token**, so they can't read live claims, and
**`members_of` is not available on every IdP** (custom IdPs may expose none). Resolved policy:

- **Floor (always works): `agents.owner_subject`** (routable per `14`) is notified unconditionally.
- **Fan-out to publishers + admins** via a **`group_members` projection** — a read-model **cache**, populated
  by whatever the adapter can do: `members_of()` on a schedule where supported, upserted from tokens on login
  elsewhere. It is explicitly **not** the authz system of record (authz reads live claims); it exists only so
  the async path can enumerate recipients. Stale/empty projection degrades to the owner-only floor, never
  silent.

```sql
-- services/api/migrations/group_members.sql — READ-MODEL CACHE for async fan-out only (16). NOT authz SoR.
create table group_members (
  group_ref   text not null,          -- native IdP ref (matches idp_binding values)
  subject     text not null,          -- OIDC subject
  source      text not null,          -- 'members_of' | 'login_seen'
  seen_at     timestamptz not null default now(),
  primary key (group_ref, subject)
);
```

## 5. Adapters & protocol scope

| Adapter | Protocol | Groups in token | Notes |
|---|---|---|---|
| **generic-oidc** | OIDC | a `groups`/`roles` claim | the portable baseline; any compliant OIDC IdP + an `idp_binding` |
| **okta** (reference) | OIDC | group **names** | `group_prefix` sugar; `members_of` via Okta API |
| **entra** (Azure AD) | OIDC | group **GUIDs** | ⚠️ claim **overage** → may need **Graph** `members_of`/`groups_for`; `exchange_obo` for Teams (12) |
| **ping** | OIDC **or SAML** | `memberOf` / attrs | OIDC first; SAML is a heavier assertion-parsing adapter |
| **cognito** | OIDC | `cognito:groups` | AWS; `members_of` via Cognito/Identity-Center APIs |
| **custom** | OIDC (documented contract) | per contract | `members_of` may return `None` → projection relies on login-seen + owner floor |

**Protocol scope (assumption — override if wrong):** **P1 = OIDC only**, adapters **generic-oidc + okta +
entra** (the common enterprise set). **SAML is adapter-shaped but deferred** (Ping/ADFS) — `verify_token`
absorbs the assertion flow when it lands. The channel-plane `exchange_obo` is Entra-only and optional.

## 6. Config

```jsonc
// org.config.json (deploy-time, 13 §2) — one active IdP per deployment (one deploy = one org).
{ "idp": { "adapter": "okta|entra|ping|cognito|generic-oidc|custom",
           "issuer": "...", "audience": "...", "jwks_uri": "...",
           "admin_group_ref": "<native-ref for the app-wide admin>",
           "api": { /* adapter-specific: Okta token, Graph app creds, etc. — vaulted (09 §9) */ } } }
```

## 7. What changes elsewhere (propagation index)

- `09 §1` roles (re-add `viewer`), `§2.1` (claim sourcing → adapter + `idp_binding`; `ap-<prefix>-*` is the
  Okta adapter's convention), `§2` (`build_principal` delegates to the adapter), `§4` RBAC (viewer row; `RANK`).
- `01`: `workspace_role` enum gains `viewer`; `workspaces.group_prefix` → `idp_binding jsonb`; new
  `group_members` cache (§4).
- `08`: `PATCH /workspaces` + settings schema use `idp_binding`; `GET /members` projects via the adapter.
- `12`: `okta.*` → `idp.*` adapter calls; `exchange_obo` is the Entra adapter's method.
- `15`: the app-wide admin is the adapter's `admin_group_ref`, not an Okta-specific claim name.
- `16`: recipient enumeration = `group_members` projection + owner-only floor (§4).
