# Branding / white-labeling

Agent Platform is deployed **one instance per organization**. The deploying organization (currently
"Trellix") is separated from the app so you can re-skin and re-identify it without hunting through code.

Two files, two roles, zero overlap:

| File | Owns | Who edits |
|---|---|---|
| `src/lib/org.ts` (`ORG`) | **What it says** — org name, product name, IdP label, email/docs domain, logo | anyone |
| `src/app/brand.css` | **How it looks** — accent palette, radius, font variables (dark + light) | a designer |

Color values live **only** in `brand.css`; identity strings **only** in `org.ts`. Neither duplicates
the other.

## Rebrand checklist

1. **Identity** — edit `src/lib/org.ts`:
   `orgName`, `productName`, `productTagline`, `productDescription`, `idpName`, `emailDomain`,
   `docsDomain`, and (optionally) `logo`.
   - The default logo is `src/components/brand-logo.tsx` — edit that SVG, or point `ORG.logo` at your own
     component.

2. **Colors / radius** — edit `src/app/brand.css`:
   - Dark theme: the `@theme { … }` block (`--color-accent*`, `--color-s1`, `--radius`, `--font-*`).
   - Light theme: the `:root.light { … }` block (`--color-accent*`, `--color-s1`).
   - You do **not** touch `globals.css` — surfaces, ink, status, and the `s2–s8` dataviz series are
     brand-neutral by design.

3. **Browser-tab marks** — replace:
   - `src/app/icon.svg` (modern SVG tab icon; carries the accent color literally since a favicon can't
     read CSS variables), and
   - `src/app/favicon.ico` (legacy binary).

4. **Font (only if the typeface changes)** — edit the `next/font` import in `src/app/layout.tsx`.
   `next/font` needs static, module-scope calls, so a CSS variable alone can't swap the family. Keep the
   `variable:` names in sync with `--font-sans`/`--font-mono` in `brand.css`.

5. **Not your concern for a real deployment** — workspaces, members, and per-workspace metrics are demo
   seed data the backend owns (the `trellix-*` workspaces in `src/lib/iam.ts` and metric maps in
   `src/lib/data.ts`). A real org gets these from IAM; leave them for the mock/demo. The default/fallback
   tenant is `DEFAULT_WORKSPACE_ID` in `src/lib/iam.ts`, derived from the seed (`WORKSPACES[0]`) — it is
   **not** a branding knob and changes only with the seed.

## Verify

```bash
npm run build   # type-check + confirm Tailwind still emits accent utilities after the theme split
npm run lint
npm run dev     # check: dark/light toggle, accent on buttons/nav, logo chip, tab icon, no flash
```

**Dry-run** a rebrand (e.g. "Acme" with an amber accent): change `org.ts` + `brand.css`, swap the two
icons, and confirm every surface flips with no code hunting. Revert when done.

See `docs/backend/13-branding.md` for the backend org-config contract (env/config vs a `GET /v1/branding`
endpoint) and the organization-vs-workspace model.
