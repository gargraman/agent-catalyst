# 13 — Branding & White-labeling (per-deployment org config)

> How Agent Platform is re-skinned and re-identified for a new deploying **organization**. The product
> ("Agent Platform") stays constant; the **organization** (currently "Trellix") is the deploying
> customer. This is a deploy-time concern, not a runtime one — it builds on the auth/tenancy planes
> (`09 §1`) but sits **above** them.

## 1. Principle: organization ≠ workspace

Two distinct levels, often confused:

- **Organization** — the deploying customer (e.g. "Trellix"). Owns branding (name, logo, colors,
  identity-provider label, email/docs domains). **One deployment per organization.**
- **Workspace** — a tenant *within* an org (e.g. `trellix-xdr`, `trellix-grc`). Owns agents, knowledge,
  members, metrics. Resolved per request from the OIDC session + the app-managed membership roster (`09 §2`).

Branding is resolved **once at deploy/build time** for the whole deployment; workspaces are resolved
**per request**. The org config is therefore not tenant-scoped and is not fetched on the hot path.

## 2. Where org config lives

**Frontend (today, mock + real):** one module is the single source of truth for identity strings.

- `frontend/src/lib/org.ts` → `ORG: OrgBrand` — `orgName`, `productName`, `productTagline`,
  `productDescription`, `idpName`, `emailDomain`, `docsDomain`, `logo`. (The default/fallback tenant is
  `DEFAULT_WORKSPACE_ID` in `iam.ts`, derived from the seed — not branding.)
- `frontend/src/app/brand.css` → the **visual tokens** (accent palette for dark + light, `--radius`,
  `--font-sans`/`--font-mono`). Kept in CSS, not `org.ts`, so colors have exactly one source of truth
  (Tailwind v4 merges this `@theme` block into the global token set).
- Assets: `frontend/src/app/icon.svg` (browser-tab mark) and `frontend/src/app/favicon.ico` (binary).

**Backend (when `services/` is built):** two viable shapes, pick per deployment model —

1. **Deploy env / config file (recommended, matches one-deploy-per-org).** The org config is baked at
   build/deploy time: env vars (`ORG_NAME`, `PRODUCT_NAME`, `IDP_NAME`, `EMAIL_DOMAIN`, …) or a mounted
   `org.config.json`. The web image reads it at build/start and populates the same `OrgBrand` shape.
   No endpoint, no per-request cost. `brand.css` is an edited file in the image.
2. **`GET /v1/branding` (only if you later go multi-tenant).** A small, cacheable, **unauthenticated**
   (or session-light) endpoint returning the `OrgBrand` JSON, plus a token-values payload the client
   applies to `:root`. Needed only if one deployment must serve multiple orgs resolved by hostname —
   explicitly **out of scope** for the current one-deploy-per-org decision.

Either way the FE **shape** (`OrgBrand`) is unchanged; only the source swaps — the same drop-in pattern
used for the rest of the mock→backend migration (`backend-integration-map.md`).

## 3. What is NOT branding

- **Workspaces, members, roles, metrics** remain backend-owned (`09`, `01`). The four `trellix-*`
  workspaces + their seed metrics are demo content (`@backend remove`), not org config. A real org gets
  its workspaces + members from the app roster (on org Okta), not from `org.ts`.
- **Status colors and the `s2–s8` dataviz series** are brand-neutral (validated palette) and deliberately
  stay in `globals.css` — do not theme them per org.

## 4. Onboarding a new org (deploy-time checklist)

The FE-side recipe (authoritative copy in `frontend/BRANDING.md`):

1. `src/lib/org.ts` — set the identity fields + (optionally) `logo`.
2. `src/app/brand.css` — set the accent family + `--radius` for dark (`@theme`) and light (`:root.light`).
3. `src/app/icon.svg` + `src/app/favicon.ico` — replace the brand marks.
4. `src/app/layout.tsx` — only if the typeface changes (next/font needs a static import).
5. Backend: supply the same `OrgBrand` values via env/config (shape 1 above).

## 5. One caveat

The typeface is the single brand attribute that can't be pure config on the FE: `next/font` requires
static, module-scope calls, so changing the real font is always an edit to `layout.tsx` (the CSS
`--font-*` vars in `brand.css` only *reference* it). Everything else is config + assets.

→ Related: `09` (auth/tenancy — the planes branding sits above), `11` (deployment — where env/config is
injected), `08` (API surface — if `GET /v1/branding` is ever added).
