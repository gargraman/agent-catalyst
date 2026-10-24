# 13 (LLD) — Branding & White-labeling  ·  *intentional stub, no full LLD*

> **Parent:** [`../13-branding.md`](../13-branding.md) (org ≠ workspace; where org config lives; the two
> backend shapes; the deploy-time onboarding checklist).
> **This is deliberately a stub, not a core/focused LLD.** The parent is already implementation-level for what
> branding is: plain per-deployment config. There is no backend logic to pin down — so this doc only records
> *why* no full LLD is warranted and lists the handful of genuinely load-bearing items a builder still needs.

## 1. Why no full LLD

Branding is **plain configuration with no model and no load-bearing backend logic** — exactly the "~90%" tier
that `scope.md` / `CLAUDE.md` say to keep as plain software. The defensible engineering lives in two places only
(the provider runtime, `02/03`; the eval/drift core, `05`); branding touches neither:

- **No runtime path.** Branding is resolved **once per deployment** (`13 §1`: one deploy per org), **not** per
  request and **not** on the invoke hot path. It never enters `runtime.invoke`, a snapshot, a cost calc, or RLS.
- **No new persistence, no tenancy.** Org config is **not** workspace-scoped (`13 §1`: org sits *above*
  workspace), so it needs **no table, no RLS policy, no DEK** — nothing the `01`/`09` LLDs would otherwise have
  to extend. It is env/config, not tenant data.
- **The frontend already owns the model.** `OrgBrand` (strings) + `brand.css` (visual tokens) are implemented and
  are the single source of truth today (§2); the backend's only job is to *supply the same values* when
  `services/` exists. A drop-in source swap, not a design problem.

Writing a full LLD here would be ceremony over a config file. The items below are the whole buildable surface.

## 2. The load-bearing items (all a builder needs)

### 2.1 The token model (parity with the frontend — do not redefine)

Two artifacts, already live; the backend must not invent a third shape:

| Artifact | Lives in | Holds | Notes |
|---|---|---|---|
| `OrgBrand` (identity strings) | `frontend/src/lib/org.ts` → `export const ORG: OrgBrand` | `orgName`, `productName`, `productTagline`, `productDescription`, `idpName`, `emailDomain`, `docsDomain`, `logo` | `logo` is a React component (`BrandLogo`), not a string — a backend source supplies the *strings*; the mark stays an FE asset |
| Visual tokens | `frontend/src/app/brand.css` (`@theme` dark + `:root.light`) | `--accent*` (dark + light), `--radius`, `--font-sans`, `--font-mono` | kept in CSS so colors have **one** source of truth (Tailwind v4 merges the `@theme` block); **not** in `org.ts` |
| Brand marks | `frontend/src/app/icon.svg`, `frontend/src/app/favicon.ico` | tab/browser marks | binary/SVG assets, replaced in the image |

**Explicitly NOT branding** (`13 §3`): workspaces/members/roles/metrics (backend-owned, `09`/`01`); the status
colors and `s2–s8` dataviz series (brand-neutral, stay in `globals.css` — never themed per org).

### 2.2 Resolution & precedence

- **Scope: per-organization / per-deployment, NOT per-workspace.** One deploy serves one org (`13 §1`), so
  branding is resolved **once at build/start**, cached for the process lifetime. **Per-workspace theming is out
  of scope** — a builder must not add a `workspace_id`-keyed brand lookup.
- **Precedence (first present wins):** explicit **env vars** (`ORG_NAME`, `PRODUCT_NAME`, `IDP_NAME`,
  `EMAIL_DOMAIN`, `DOCS_DOMAIN`, …) → mounted **`org.config.json`** → built-in **defaults** baked in the image.
  This matches the `pydantic-settings` env convention (`00 §6`); treat these as boot-time infra config
  alongside the rest of `Settings`, injected at deploy (`11 §5`).
- **Multi-tenant branding (`GET /v1/branding`) is explicitly deferred** (`13 §2` shape 2): needed only if one
  deployment ever serves multiple orgs by hostname. Not for the current one-deploy-per-org decision. If added
  later it is a small, cacheable, session-light endpoint returning the same `OrgBrand` JSON + a token payload —
  **the shape does not change**, only the source.

### 2.3 Serving & frontend consumption (keep the shape identical)

- **Storage:** env / mounted config file in the image (shape 1, recommended, `13 §2`). No database.
- **Served:** the `web` image reads env/config at build/start and populates the **same `OrgBrand` shape** the
  mock uses today — the identical drop-in pattern as the rest of the mock→backend migration
  (`backend-integration-map.md`): keep the type, swap the source. The `@backend` marker already in `org.ts`
  documents exactly this.
- **The one caveat (`13 §5`):** the typeface can't be pure config — `next/font` requires static, module-scope
  calls, so changing the real font is always an edit to `frontend/src/app/layout.tsx`; the CSS `--font-*` vars in
  `brand.css` only *reference* it. Everything else is config + assets.

## 3. Cross-refs

- **`13` (parent)** — the full org-vs-workspace model + the deploy-time onboarding checklist (authoritative FE
  copy in `frontend/BRANDING.md`).
- **`11` (deployment)** — where the env/config is injected (`11 §5` config & secrets boot); branding is boot-time
  config, not a secret.
- **`09` (auth/tenancy)** — the planes branding sits *above*; `idpName` is a display string only, unrelated to
  the OIDC/RBAC machinery.
- **`08` (API surface)** — only if `GET /v1/branding` is ever added (deferred, §2.2).

> **Verification:** frontend grepped for parity — `frontend/src/lib/org.ts` (`OrgBrand` type + `ORG`),
> `frontend/src/app/brand.css` (`--accent*`/`--radius`/`--font-*` tokens), and the `icon.svg`/`favicon.ico`
> marks — all match `13 §2`. No contradiction with `09`/`01` (branding adds no table, RLS, or tenancy) or
> `scope.md` (this is the plain-software tier, no defensible core touched). Stub status is intentional.
