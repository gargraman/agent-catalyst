import type { ComponentType } from "react";
import { BrandLogo } from "@/components/brand-logo";

/* ============================================================
   Organization branding — the ONE place for org identity strings.
   This separates the deploying organization (currently "Trellix")
   from the product ("Agent Platform") and from the app code.

   "Organization" sits ABOVE "workspace": the trellix-xdr/grc/labs/people
   workspaces are tenants WITHIN the org; the org is the deploying customer.

   To rebrand for a new org, edit the values below (strings + logo) and the
   color tokens in src/app/brand.css. See frontend/BRANDING.md for the full
   checklist. Visual tokens (colors/radius/fonts) are NOT here — they live in
   src/app/brand.css, so colors have exactly one source of truth.

   @backend: a real deployment serves this per-deployment branding from env /
   config or a `GET /v1/branding` endpoint (same field shape). Workspaces and
   memberships remain backend-owned and are unaffected by this module.
   ============================================================ */

export type OrgBrand = {
  /** Deploying organization display name, e.g. "Trellix". */
  orgName: string;
  /** Product name shown in the shell wordmark and page title. */
  productName: string;
  /** Short suffix after the product name in the browser tab title. */
  productTagline: string;
  /** Long description for page metadata. */
  productDescription: string;
  /** Identity-provider label shown in access/IAM copy, e.g. "Trellix IAM". */
  idpName: string;
  /** Email domain used by seed members, e.g. "trellix.com". */
  emailDomain: string;
  /** Docs domain referenced by seed knowledge sources, e.g. "docs.trellix.com". */
  docsDomain: string;
  /** Brand mark component (inherits currentColor). Swap in src/components/brand-logo.tsx. */
  logo: ComponentType<{ className?: string }>;
};

export const ORG: OrgBrand = {
  orgName: "Trellix",
  productName: "Agent Platform",
  productTagline: "Build, test & publish LLM agents",
  productDescription:
    "Internal platform to build, configure, test, and publish LLM-powered agents with guardrails, model-agnosticism and drift-aware evaluation.",
  idpName: "Trellix Okta",
  emailDomain: "trellix.com",
  docsDomain: "docs.trellix.com",
  logo: BrandLogo,
};
