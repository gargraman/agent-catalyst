# 18 (LLD) — Identity Adapters (IdP-agnostic authN → `Principal`)

> **⚠️ Secret custody — as built (feature 014, 2026-11-11):** secret custody now runs through a pluggable secrets backend (**AWS Secrets Manager** / LocalStack / `local`), **not** HashiCorp Vault Transit. The `transit/keys/platform-idp` / Vault adapter-credential detail in this LLD is the **superseded design baseline** for the still-unbuilt IdP-adapter credential family — to be re-reconciled when built. See `docs/scope.md` (2026-11-11) and [`../09-security-tenancy.md §9`](../09-security-tenancy.md).

> **Parent:** [`../18-identity-adapters.md`](../18-identity-adapters.md) (the `IdentityProvider` adapter
> boundary, `workspaces.idp_binding`, the `group_members` projection cache, the adapter table, the deploy-time
> config shape, the propagation index).
> The parent fixes **the boundary and the contract**; it leaves the load-bearing *mechanisms* at HLD altitude:
> `Claims` is named but never defined, `verify_token` is `...` (no JWKS cache, no issuer/audience/signature/expiry/
> clock-skew rules), `build_principal` is `...` (no claim→role/team partition, no highest-role rule, no
> `idp_binding` reverse map), the **executer non-production** rule is implicit, session/refresh is unstated, and
> *how* a built `Principal` reaches `SET LOCAL app.workspace_id` + `can_use` is split across `09`. This doc makes
> those buildable. **Design only — nothing built.**

## What this adds over parent `18`

| Gap in `18` | Filled here |
|---|---|
| `Claims` named in the Protocol (`§2`) but never defined; `Principal` lives in `09 §2` | §1 — the `Claims` type, the `IdpConfig`/`BindingMap` types, and the adapter factory/DI (`get_idp`) |
| `verify_token(...) -> Claims: ...` — no JWKS cache, no iss/aud/sig/exp/nbf, no clock skew, no alg allowlist | §2 — the generic-OIDC `verify_token`: JWKS cache + kid refresh, the full claim checks, leeway, `alg`-confusion defense |
| `build_principal(...) -> Principal: ...` — the native-group → role/team mapping is prose (`§3`) | §3 — the `idp_binding` reverse map, the highest-role rule (mirrors `iam.ts roleFor`), the three role-vocabulary split, the **executer-is-never-produced-here** rule |
| adapters named in a table (`§5`); concrete bodies absent | §4 — okta (`group_prefix` expansion + `members_of`), entra (GUIDs + claim-overage → Graph + `exchange_obo`), generic baseline |
| session establishment / refresh / revocation lag unstated | §5 — console cookie vs bearer, the stateless re-verify model, refresh cadence as the revocation bound |
| "the core consumes a normalized `Principal`" — *how* it flows into tenancy + access policy not shown here | §6 — the `get_principal → get_workspace_ctx → set_tenant_guc → can_use` chain (ties `09` LLD §1) |
| `idp_binding`/`viewer`/`group_members` deltas; adapter API-cred custody | §7 — the **already-adopted** `01` deltas + the typed `IdpBinding` model, the `group_members` repo, the adapter-cred DEK scope |
| error taxonomy + edge cases (unknown claim, no workspace, disabled user, JWKS outage, claim overage) | §8 — auth failures as HTTP-surfacing (`00 §1` cat. A) + the edge-case table |

Python 3.12, `from __future__ import annotations`, SQLAlchemy **`AsyncSession`** on the request path
([`00-conventions.lld.md §4`](00-conventions.lld.md)). Snippets compile (§9). `Principal` (`09 §2`),
`AuthRequired`/`AccessDenied`/`NotFound` ([`00-conventions.lld.md §1`](00-conventions.lld.md)),
`set_tenant_guc`/`get_workspace_ctx` ([`09-security-tenancy.lld.md §1`](09-security-tenancy.lld.md)),
`VaultEnvelope` (`09` LLD §3): **see those docs — not restated.**

---

## 1. The normalized types + the adapter factory

The authz core (`09`) consumes **only** `Principal`; the adapter boundary (`18 §1`) produces it from a
vendor token. Two types sit on that boundary — `Claims` (the validated token, minimally normalized) and
`Principal` (`09 §2`, unchanged) — plus the per-deployment config and the cached binding map.

```python
from __future__ import annotations
from dataclasses import dataclass, field, replace

from auth.principal import Principal          # 09 §2 — unchanged (subject_id/email/workspace_roles/is_admin/teams)


@dataclass(frozen=True)
class Claims:
    """The validated token, normalized to the minimum the core needs. `groups` is the RAW native ref set
    (vendor-shaped: Okta names, Entra GUIDs, SAML DNs) — the adapter, not the core, interprets it (18 §1)."""
    subject: str                         # OIDC `sub`
    email: str
    email_verified: bool                 # platform JWT claim — load-bearing for channel federation (12 §4)
    display_name: str
    groups: frozenset[str]               # every native group/role ref present in the token
    raw: dict                            # full validated payload (adapters read vendor extras, e.g. _claim_names)


@dataclass(frozen=True)
class IdpConfig:
    """From org.config.json (13 §2); one active IdP per deployment (18 §6)."""
    adapter: str                         # 'generic-oidc' | 'okta' | 'entra' | 'ping' | 'cognito' | 'custom'
    issuer: str
    audience: str
    jwks_uri: str
    admin_group_ref: str                 # the one app-wide admin claim (15 / 18 §6); Okta default 'ap-platform-admin'
    groups_claim: str = "groups"         # the token claim carrying native group refs (generic baseline, 18 §5)
    api: dict = field(default_factory=dict)   # adapter API creds REF (vaulted, §7) — NEVER the raw secret


class BindingMap:
    """Cached {workspace_id: idp_binding} from the workspaces registry. Small, rarely changes; invalidated on
    a workspace idp_binding edit (08 PATCH /workspaces). Read WITHOUT a tenant GUC — `workspaces` is the tenant
    registry, NOT a WorkspaceScoped child (it is deliberately absent from the 09 LLD §2.2 RLS loop), so a
    cross-workspace read here is legitimate (the principal is being built BEFORE a workspace is selected)."""
    def __init__(self, by_workspace: dict[str, dict]):
        self.by_workspace = by_workspace
```

The **active adapter is chosen once per deployment** and injected via FastAPI `Depends` — the same single-active
pattern the platform uses for the provider gateway (`02`) and `KeyVault` (`09 §9`):

```python
from functools import lru_cache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ADAPTERS = {"generic-oidc": GenericOidcAdapter, "okta": OktaAdapter, "entra": EntraAdapter}
# ping/cognito/custom: adapter-shaped, deferred (18 §5); registered here when they land.


@lru_cache(maxsize=1)
def get_idp() -> "IdentityProvider":
    cfg = load_idp_config()                      # org.config.json (13 §2); cfg.api creds resolved from Vault (§7)
    return _ADAPTERS[cfg.adapter](cfg)


async def load_binding_map(db: AsyncSession) -> BindingMap:
    rows = (await db.execute(text("select id, idp_binding from workspaces"))).all()
    return BindingMap({str(r.id): (r.idp_binding or {}) for r in rows})   # not RLS-scoped (see BindingMap)
```

---

## 2. `verify_token` — the generic-OIDC baseline (fills `18 §2`)

The portable adapter every compliant OIDC IdP reuses. It is the one place a token's signature is trusted, so
every check is explicit — a missing one is a silent auth bypass.

```python
import time
import jwt                               # PyJWT
from jwt import PyJWKClient

_LEEWAY_S = 60                           # clock-skew tolerance for exp/nbf/iat (design choice; ≤ a token lifetime)
_ALLOWED_ALGS = ["RS256", "ES256"]       # ASYMMETRIC only — reject 'none' and HS* (alg-confusion / key-confusion defense)


class GenericOidcAdapter:
    def __init__(self, cfg: IdpConfig):
        self.cfg = cfg
        # JWKS CACHE: keyed by `kid`, refetched on an unknown kid (key rotation) and on a 1h lifespan. One client
        # per adapter instance = one cache shared across requests; the IdP rotates keys on its own cadence.
        self._jwks = PyJWKClient(cfg.jwks_uri, cache_keys=True, lifespan=3600)

    async def verify_token(self, raw: str) -> Claims:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(raw)   # kid lookup; refetches JWKS on a new kid
            payload = jwt.decode(
                raw, signing_key.key, algorithms=_ALLOWED_ALGS,
                audience=self.cfg.audience, issuer=self.cfg.issuer,   # aud + iss pinned
                leeway=_LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},  # presence enforced, not just value
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthRequired("token expired") from exc             # 401 (00 §1 cat. A)
        except jwt.InvalidTokenError as exc:                         # bad sig / iss / aud / alg / nbf
            raise AuthRequired("token validation failed") from exc
        except jwt.PyJWKClientError as exc:                          # JWKS unreachable / no matching key
            # Fail CLOSED: we cannot verify → not authenticated. The 1h JWKS cache absorbs a brief outage; a
            # sustained one is an ops alert (10), not a silent downgrade. (§8 edge case.)
            raise AuthRequired("cannot verify token signature") from exc
        return self._to_claims(payload)

    def _to_claims(self, p: dict) -> Claims:
        return Claims(
            subject=p["sub"], email=p.get("email", ""),
            email_verified=bool(p.get("email_verified", False)),
            display_name=p.get("name", p.get("email", "")),
            groups=frozenset(p.get(self.cfg.groups_claim, [])),
            raw=p,
        )
```

- **No bind-time trust in the client's workspace selection** — `verify_token` authenticates the *token*; the
  workspace is chosen later and authorized by the role claim (`09 §3`, "selection is not authority").
- **SAML** (Ping/ADFS, deferred `18 §5`) absorbs its assertion-parse flow behind this same `verify_token`
  signature — a heavier body, identical output contract (`Claims`).

---

## 3. `build_principal` — native groups → `Principal` (fills `18 §2/§3`)

This is where the **three role vocabularies the `01` LLD §7.2 flags** are kept distinct — conflating them is a
real bug (§7.2: "validating `access_policy.use_roles` against the `workspace_role` enum would reject every
`admin`/`executer` policy"). The adapter produces exactly:

- `workspace_roles: {ws_id: viewer|builder|publisher}` — the **3-value `workspace_role` enum** (`01 §3`), one
  per workspace the user holds a role claim for, **highest claim wins** (mirrors `iam.ts roleFor`/`memberRoleFor`);
- `is_admin` — the single app-wide claim (`admin_group_ref`), **not** a `workspace_role` value;
- `teams` — the **functional** IdP groups (a separate namespace, `09 §2.1`).

It never produces **`executer`**: that role is the *caller plane* (`09 §1/§6`, `12 §4`) minted for bearer
tokens and channel federation, not derived from a console OIDC session. The console adapter path yields only
`{viewer, builder, publisher}` + `is_admin`.

```python
_WS_ROLE_RANK = ("viewer", "builder", "publisher")   # ascending — mirrors iam.ts WS_ROLE_RANK exactly


def _highest_role(groups: frozenset[str], binding: dict, consumed: set[str]) -> str | None:
    """The highest workspace role the user's native refs confer for ONE workspace (iam.ts roleFor rule:
    iterate ascending, keep the last/highest match). Records the refs it consumed so they don't fall into teams."""
    held: str | None = None
    for role in _WS_ROLE_RANK:
        ref = binding.get(role)                      # idp_binding value for this role (native ref)
        if ref and ref in groups:
            held = role
            consumed.add(ref)
    return held


class _PrincipalBuilderMixin:
    cfg: IdpConfig

    async def build_principal(self, claims: Claims, *, bindings: BindingMap) -> Principal:
        ws_roles: dict[str, str] = {}
        consumed: set[str] = set()
        for ws_id, binding in bindings.by_workspace.items():
            role = _highest_role(claims.groups, binding, consumed)
            if role is not None:
                ws_roles[ws_id] = role
        is_admin = self.cfg.admin_group_ref in claims.groups
        if is_admin:
            consumed.add(self.cfg.admin_group_ref)
        # TEAMS = the functional remainder: every group ref NOT consumed as a workspace-role or admin claim.
        # (An adapter MAY instead read a distinct teams claim; the generic baseline partitions the groups set.)
        teams = frozenset(g for g in claims.groups if g not in consumed)
        return Principal(
            subject_id=claims.subject, email=claims.email, display_name=claims.display_name,
            workspace_roles=ws_roles, is_admin=is_admin, teams=teams,
        )
```

### 3.1 `idp_binding` as the mapping layer (not `group_prefix`)

`workspaces.idp_binding` (`01 §4.1`, already adopted) is the per-workspace `{role: native_ref, teams: {...}}`
map (`18 §3`). The reverse lookup above reads it directly, so **every adapter works off explicit native refs**
— GUIDs (Entra), DNs (SAML), names (Okta). The Okta `ap-<prefix>-<role>` **name convention survives only as
sugar** that *expands* into `idp_binding` at workspace-config write time (§4); `build_principal` never special-cases
it. A native ref present in the token but **absent from every binding and not the admin ref** confers no role —
it is an **unknown claim** and simply lands in `teams` (or is ignored); never an error (§8).

---

## 4. Concrete adapters (fills `18 §5`)

```python
import okta_api, graph_api             # thin vendor API clients, the ONLY place a vendor SDK is imported (18 §1)


class GenericOidcAdapter(_PrincipalBuilderMixin):
    # verify_token + _to_claims: §2. build_principal: §3. The portable baseline.
    async def resolve_subject(self, email: str) -> str | None:
        """email → the IdP `sub` (NOT the email), equal to the OIDC `sub` the SAME human gets on an interactive
        login (claims.subject). This is the cross-plane key: the channel plane (12 §4) calls this so it keys
        per-user MCP connections (17 §3) IDENTICALLY to the console — "connect once, reuse everywhere"
        (17 LLD §5/§6) holds only if this returns the console `sub`. Generic baseline: a directory/userinfo
        lookup BY EMAIL that returns `sub`."""
        rec = await self._directory_lookup(email)          # OIDC userinfo / directory query keyed by email
        if rec is not None and rec.get("sub"):
            return rec["sub"]
        # FALLBACK — a bare OIDC IdP may expose no reliable email→sub map. Return None; the caller (12 §4) treats
        # a None subject as "cannot federate this user to a per-user MCP connection" and prompts an interactive
        # connect per plane. NEVER return the email as a stand-in: an email is not the `sub` namespace, so it would
        # key a connection the console can never reuse (17 LLD §6) — the silent-mismatch bug we are closing.
        return None

    async def _directory_lookup(self, email: str) -> dict | None: ...   # IdP-specific; None if unsupported
    async def groups_for(self, subject: str) -> set[str]: ...
    async def members_of(self, group_ref: str) -> list[str] | None:
        return None                      # generic baseline exposes no enumeration API → projection relies on login-seen (18 §4)


class OktaAdapter(GenericOidcAdapter):
    """Reference adapter. Groups are NAMES; the `ap-<prefix>-<role>` convention is sugar that EXPANDS into
    idp_binding at workspace-config write (18 §3) — build_principal still reads the explicit refs."""
    @staticmethod
    def expand_prefix(group_prefix: str) -> dict:
        # 08 PATCH /workspaces writes this into workspaces.idp_binding when the admin supplies a group_prefix.
        return {r: f"ap-{group_prefix}-{r}" for r in _WS_ROLE_RANK}

    async def resolve_subject(self, email: str) -> str | None:
        # Okta Users API: email → the user's Okta id, which IS the OIDC `sub` Okta mints (stable, non-pairwise),
        # so the channel-resolved subject equals the console `claims.subject`. None if the user isn't found.
        return await okta_api.user_sub(self.cfg.api, email)

    async def members_of(self, group_ref: str) -> list[str] | None:
        return await okta_api.group_members(self.cfg.api, group_ref)   # subjects → group_members cache (18 §4)


class EntraAdapter(GenericOidcAdapter):
    """Azure AD/Entra. Groups are object GUIDs; a large group set is OMITTED from the token (claim overage),
    signalled by `_claim_names.groups` → must be fetched from Microsoft Graph."""
    def _to_claims(self, p: dict) -> Claims:
        base = super()._to_claims(p)
        if self.cfg.groups_claim not in p and "_claim_names" in p:     # overage: groups not inlined
            return replace(base, groups=frozenset())                   # left empty; resolved lazily via groups_for (Graph)
        return base

    async def groups_for(self, subject: str) -> set[str]:
        return set(await graph_api.member_groups(self.cfg.api, subject))   # GUIDs via Graph

    async def members_of(self, group_ref: str) -> list[str] | None:
        return await graph_api.group_members(self.cfg.api, group_ref)

    async def resolve_subject(self, email: str) -> str | None:
        # CAVEAT: Entra's OIDC `sub` is PAIRWISE (unique per app+user) and is NOT recoverable from a directory
        # lookup — Graph returns the stable `oid`, not the app-scoped `sub`. So email→sub cannot be reproduced.
        # RESOLUTION (required for cross-plane reuse to hold, 17 LLD §5/§6): pin the canonical subject to the
        # non-pairwise `oid` in BOTH planes — the deployment maps the console `claims.subject` to `oid` (optional
        # `sub` claim config, else _to_claims reads `oid`), and this returns the same `oid`. If that mapping is not
        # configured, email→sub is NOT reliable → return None (per-plane re-connect), never a mismatched id.
        return await graph_api.user_oid(self.cfg.api, email)

    async def exchange_obo(self, token: str, audience: str) -> str:
        return await graph_api.on_behalf_of(self.cfg.api, token, audience)  # Teams OBO (12 §4); Entra-only
```

> **Claim-overage handling is load-bearing for Entra.** When `build_principal` gets `claims.groups == {}`
> because of overage, `workspace_roles`/`teams` would be empty — so the Entra path must **enrich via `groups_for`
> before** partitioning: `claims = replace(claims, groups=frozenset(await idp.groups_for(claims.subject)))` when
> overage is detected. `get_principal` (§6) does this enrichment step for adapters that signal it; the generic
> path skips it (groups are inline). This is the one place where authN needs a second IdP round-trip.

---

## 5. Session establishment, refresh & revocation lag

- **Console (cookie).** Auth.js (`09 §2`) holds the OIDC session; the cookie is `httpOnly`/`SameSite=Lax`/signed.
  The **backend keeps no server session store** — it re-runs `verify_token` on the session's access token every
  request (§2). State-changing routes add a CSRF double-submit token (`09 §2`).
- **API bearer clients.** A non-cookie client sends the OIDC access token in `Authorization: Bearer …`; the
  **same** `verify_token` path applies. (This is the console/admin OIDC plane — distinct from the *caller*
  bearer token of `09 §6`, which is our own minted token, not an IdP token.)
- **Refresh.** Auth.js refreshes the access token with the IdP refresh token before expiry; the backend just
  verifies the new token. **No SCIM / directory sync** (`09 §2.1`): claims are read live from the token.
- **Revocation lag + the deny-set escape hatch.** Because authZ reads claims from the token, an IdP-side
  grant/revoke (role change, **disabled user**) takes effect **at the next token refresh/login**, not instantly —
  the *passive* bound. The standing mitigation is a **short access-token TTL** (deployment-configured, ≤1h) so a
  passively-disabled user is locked out within one TTL. For *active* cut-off (incident, stolen token, an
  offboarding we're told about), an **admin `force-logout` / `revoke-user`** writes the subject/token to a
  **transient Redis deny-set** checked on this same `verify_token` path, **failing closed** → the session dies on
  its next request (`09 §2.1`, `09 §6`). This is a *transient revocation list*, **not** an app-side
  roster/deactivation store and **not SCIM** — automated IdP-pushed offboarding stays out of scope. (Amends the
  2026-10-22 rev 1/2 "no app-side deactivation list" bound; 2026-10-25.)

---

## 6. How the `Principal` flows into tenancy + access policy

The adapter produces a `Principal`; the chain that turns it into a tenant-pinned, policy-gated request is in
`09` — this is the single picture of the seam (the parent `18 §1` draws the boundary; here is the call order).

```python
# services/api/deps.py — the dependency chain (09 LLD §1.1 is the tenancy half).
from fastapi import Request, Depends


async def get_principal(request: Request, db: AsyncSession = Depends(get_db),
                        idp: "IdentityProvider" = Depends(get_idp)) -> Principal:
    token = _session_token(request)                      # cookie or Authorization
    if not token:
        raise AuthRequired("no session")                 # 401 (00 §1)
    claims = await idp.verify_token(token)               # §2
    if _overage(claims):                                 # Entra claim-overage → enrich via Graph (§4)
        claims = replace(claims, groups=frozenset(await idp.groups_for(claims.subject)))
    bindings = await load_binding_map(db)                # workspaces registry, not RLS-scoped (§1)
    return await idp.build_principal(claims, bindings=bindings)   # §3
```

The rest is unchanged from `09`: `get_workspace_ctx` (`09 §3`) reads the client-selected `ws_id`, calls
`principal.role_in(ws_id)` (→ `admin` if `is_admin`, else the mapped `workspace_role`, else `None` → `403`),
validates existence on the admin path (`09 §3`), then `await set_tenant_guc(db, ws_id)` (`09` LLD §1.1) pins the
GUC so **RLS** (`09` LLD §2) filters every later query.

At invoke, the runtime gate `can_use`/`can_manage` (`09 §5`) consumes `ctx.principal` — **but that is a *different
type* from the `Principal` this adapter builds.** `build_principal` (§3) returns the **auth `Principal`** (`09 §2`:
`{subject_id, email, workspace_roles: dict, is_admin, teams}`) — the console-plane identity carrying a *role map*
over every workspace. The runtime carries the **access-policy `Principal`** (`01` LLD §2, sibling-owned:
`{role: str, teams: list[str], subject: str | None}`) — a *single resolved role* for the *selected* workspace,
not a map. They are **not interchangeable**; conflating them is a real bug (the same role-vocabulary hazard
`01` LLD §7.2 flags). The two are bridged by a **one-line conversion at the invoke seam** — the caller
construction where `RunContext` is built (console/channel/token callers, `03` / `12 §4` / `09 §6`):

```python
# services/api/deps.py (and the channel/token caller builders, 12 §4 / 09 §6) — the INVOKE SEAM conversion.
from access_policy import Principal as AccessPrincipal   # 01 §2 (sibling-owned): {role, teams, subject} — NOT auth.Principal


def to_access_principal(auth_p: Principal, selected_ws: str, *, caller_role: str | None = None) -> AccessPrincipal:
    """Collapse the AUTH Principal (09 §2: subject_id/email/workspace_roles/is_admin/teams) to the access-policy
    Principal the runtime carries as ctx.principal (01 §2: role/teams/subject). Done once, where RunContext is built."""
    role = caller_role or ("admin" if auth_p.is_admin else auth_p.role_in(selected_ws))  # "executer" for channel/token callers (12 §4, 09 §6)
    return AccessPrincipal(role=role, teams=list(auth_p.teams), subject=auth_p.subject_id)
```

- `role` collapses the role *map* to the selected workspace (`admin` if `is_admin`, else the mapped
  `workspace_role`); **caller-plane** channel/token invocations instead pin `caller_role="executer"` (`09 §6`,
  `12 §4`) — the one role `build_principal` never produces (§3).
- `teams = auth_p.teams`; `subject = auth_p.subject_id` (the IdP `sub`, which is also the `per_user` MCP connection
  key, `17` LLD §3).
- The access-policy `Principal` is **defined in `01` LLD §2** (sibling-owned) — referenced here, not redefined.
  The adapter is the only thing that changed; the authz core never learns the IdP.

```
  token ─ verify_token(§2) ─► Claims ─ build_principal(§3) ─► AUTH Principal (09 §2) = {subject_id, email,
                                                              │                         workspace_roles, is_admin, teams}
         get_workspace_ctx(09§3): role_in(ws) ─► set_tenant_guc(09 LLD §1) ─► RLS(09 LLD §2)
                                                              │
         invoke seam: to_access_principal(§6) ─► access-policy Principal (01 §2) = {role, teams, subject}
                                                              │
         invoke/read: can_use/can_manage(09§5)  ◄─ role + teams + subject
```

---

## 7. Data model deltas

Most are **already adopted in the parent `01`** (do not re-propose them): `workspaces.idp_binding jsonb`
(`01 §4.1`), `workspace_role` already includes **`viewer`** (`01 §3`: `enum('viewer','builder','publisher')`),
and the `group_members` cache is specified in `18 §4`. This LLD adds the **typed model + repo + cred custody**.

### 7.1 `IdpBinding` typed model (over the `idp_binding` jsonb)

```python
from pydantic import BaseModel, ConfigDict


class IdpBinding(BaseModel):
    """Typed view of workspaces.idp_binding (01 §4.1). Roles map to native refs; teams is a sub-map.
    Values are opaque native refs (names|GUIDs|DNs) — never validated against a vendor scheme (18 §3)."""
    model_config = ConfigDict(extra="forbid")
    viewer: str | None = None
    builder: str | None = None
    publisher: str | None = None
    teams: dict[str, str] = {}          # {team_name: native_ref}
```

The app-wide **admin** ref is **not** in `idp_binding` (it is deployment config, `IdpConfig.admin_group_ref`,
`18 §3/§6`) — a per-workspace binding never confers admin.

### 7.2 `group_members` cache repo (read-model for async fan-out, `18 §4`)

The table is in `18 §4` (a **cache, not authz SoR**). The repo is `WorkspaceScoped`-adjacent but keyed by
`group_ref`; the notifier (`16`) reads it, the adapter populates it:

```python
class GroupMembersRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_seen(self, group_ref: str, subject: str, source: str) -> None:
        # 'login_seen' on each login (any adapter); 'members_of' on a schedule where the adapter supports it.
        await self.db.execute(text(
            "insert into group_members (group_ref, subject, source) values (:g, :s, :src) "
            "on conflict (group_ref, subject) do update set seen_at = now(), source = excluded.source"
        ), {"g": group_ref, "s": subject, "src": source})

    async def members(self, group_ref: str) -> list[str]:
        rows = (await self.db.execute(
            text("select subject from group_members where group_ref = :g"), {"g": group_ref})).all()
        return [r.subject for r in rows]      # stale/empty → notifier degrades to the owner-only floor (18 §4)
```

### 7.3 Adapter API creds → the **platform** DEK scope

Okta's API token and Entra's Graph app secret (`IdpConfig.api`, used by `members_of`/`groups_for`/`exchange_obo`)
are **deployment-wide** (one deploy = one org = one IdP, `18 §6`), never per-workspace. So they do **not** use a
per-workspace DEK — they use a **platform-scope** key under the `09` LLD §3 envelope, a dedicated
`transit/keys/platform-idp` (separate from `platform-pool`/provider keys for blast-radius isolation). Only
`{wrapped_dek,nonce,ct,key}` is persisted (`09` LLD §3.2); `load_idp_config` decrypts them JIT at boot and the
adapter holds them in memory. They are never per-workspace secrets, so they are **outside** the tenant RLS /
per-workspace-DEK suite entirely.

---

## 8. Error taxonomy & edge cases

**Auth failures are HTTP-surfacing (`00 §1` category A)** — they produce the `08` error envelope, never a run
`stop_reason` (identity resolves *before* any run exists):

| Condition | Class (00 §1) | HTTP |
|---|---|---|
| no/empty session token | `AuthRequired` | 401 `auth.required` |
| bad signature / iss / aud / alg / nbf | `AuthRequired` | 401 |
| expired token (beyond `_LEEWAY_S`) | `AuthRequired` | 401 |
| JWKS unreachable (sustained; cache miss) | `AuthRequired` (fail closed) + ops alert | 401 |
| authenticated, but **no role claim** for the selected workspace | `AccessDenied` (via `get_workspace_ctx`, `09 §3`) | 403 `access.denied` |
| authenticated admin, **workspace id unknown** | `NotFound` (admin path only, `09 §3`) | 404 `not_found` |

**Edge cases & idempotency:**

- **Unknown claim** — a native ref in the token matching no `idp_binding` role/team and not `admin_group_ref`:
  confers nothing, falls into `teams` (functional remainder, §3) or is ignored. **Never an error** — IdP group
  sets drift, and a stray group must not 500 a login.
- **No workspace** — a user with zero workspace role claims and not admin: `workspace_roles == {}`,
  `is_admin == False`. `get_principal` succeeds (they are authenticated); every `get_workspace_ctx` returns
  `403` → the FE shows the no-access gate (mirrors `auth.tsx hasAccess == false`). Authentication without
  authorization is the correct, non-leaky state.
- **Disabled user** — *passively* handled IdP-side: our side sees an invalid/expired token → `401` within one
  access-token TTL (§5). For *active* cut-off, an admin `force-logout`/`revoke-user` adds the subject to the
  **transient Redis deny-set** (§5, `09 §2.1`) → `401` on the next request. The deny-set is a transient revocation
  list, not a persistent app-side roster/deactivation store (no SCIM).
- **Claim overage (Entra)** — detected in `_to_claims`; `get_principal` enriches via `groups_for` before
  `build_principal` (§4/§6). A Graph failure here is the JWKS-outage analog: fail closed to `401` + alert.
- **Concurrent first-login** — `BindingMap` is read-only and cacheable; `group_members.upsert_seen` is an
  idempotent `ON CONFLICT` upsert, so parallel logins of the same user converge without a race.
- **`idp_binding` edit** — invalidates the cached `BindingMap` (and `get_idp`'s `lru_cache` is config-scoped, not
  per-request); the next `get_principal` reloads. No stored per-user role to migrate (claims are the SoR).

---

## 9. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the `00`–`09` LLDs); one Pydantic
   model + dataclasses + the OIDC/JWT flow (import-stubbed for `jwt`, `okta_api`, `graph_api`).
2. **Cross-ref** — against `18` read in full (adapter Protocol §2, `idp_binding` §3, `group_members` §4, adapter
   table §5, config §6, propagation index §7); `09 §1/§2/§2.1/§3/§5` (planes, `Principal`, claim sourcing, the
   tenancy guard, `can_use`); `09` LLD §1 (`set_tenant_guc`), §2 (RLS loop — `workspaces` deliberately absent),
   §3 (DEK envelope/scopes); `01 §3` (`workspace_role` already has `viewer`), `01 §4.1` (`idp_binding`), `01` LLD
   §2 (the access-policy `Principal` `ctx.principal` — a *distinct type* from the auth `Principal`, bridged at the
   invoke seam, §6), §7.2 (the three role vocabularies); `12 §4` (channel federation reuses `resolve_subject`
   — now pinned to the IdP `sub` namespace, §4, for cross-plane MCP reuse `17` LLD §5/§6 — plus `groups_for`/
   `exchange_obo`); `16`/`18 §4` (recipient enumeration); `15`/`18 §6` (admin claim); frontend
   `iam.ts` (`roleFor`/`memberRoleFor`/`idpBinding`/`WS_ROLE_RANK`), `auth.tsx` (`hasAccess`, platform-admin),
   `permissions.ts`/`access-policy.ts` (the `Role`/`CallerRole` split).
3. **No already-adopted convention presented as unadopted** — `idp_binding`, `viewer`, and `group_members` are
   framed as **already in `01`/`18`** (§7); this doc adds only the typed model, the repo, and the cred DEK scope.
4. **Role-vocabulary & Principal-type non-conflation** — verified the adapter emits **only**
   `{viewer,builder,publisher}` + `is_admin`, never `executer` (the caller plane, `09 §6`/`12 §4`), matching
   `01` LLD §7.2; and that `build_principal`'s **auth `Principal`** (`09 §2`) is a *distinct type* from the
   runtime's **access-policy `Principal`** (`01` LLD §2), bridged by the invoke-seam `to_access_principal`
   conversion (§6) — the earlier §6 "same `Principal`" claim was wrong and is corrected.
5. **`resolve_subject` pinned** — no longer `...`: the generic adapter returns the IdP `sub` via a directory/
   userinfo lookup with an explicit `None` fallback; Okta overrides to its directory `sub`; Entra overrides to the
   stable `oid` with the pairwise-`sub` caveat stated (§4). This is the cross-plane key for `17` LLD §5/§6.

→ Sibling: [`17-mcp-identity.lld.md`](17-mcp-identity.lld.md) (per-user MCP identity reuses this adapter's
subject + the Entra `exchange_obo`).
