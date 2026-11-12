# Feature Specification: M5 — IdP Group → Members-Store Sync + Notification Projection

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `020-m5-idp-group-sync`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P2 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015e).

> **Shared design**: [research R-note (authority reconciliation)](../015-m5-security-completion/research.md) ·
> [contract](../015-m5-security-completion/contracts/console-auth.md) ·
> [data-model (`group_members`, `idp_binding`)](../015-m5-security-completion/data-model.md).
> **Dependencies**: 019 (sync runs on login).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Access and notifications reflect IdP group membership (Priority: P2)

An admin maps the org's IdP groups to workspace roles and teams. On sign-in, that mapping **syncs the user's role
and team into the members store** — the authority for role/team under RLS (per 012/013). The platform never
hard-codes a vendor's group-naming convention. A best-effort projection of group membership (populated from
whatever the IdP exposes) drives notification fan-out.

**Why this priority**: Makes authZ and notification fan-out track real org membership across heterogeneous IdPs
(name / object-GUID / SAML attribute). P2 because it builds on login (019) and the adapter seam (012/18).

> **Clarified 2026-11-11:** authority is the **members store**, not live IdP group claims — reconciling with
> 012/013 and the pilot's claim-less Trellix-IAM token. This is a group→members-store **sync**, not a
> per-request group→role resolver.

**Independent Test**: Configure a workspace role→group/team→group mapping, sign in a user whose groups match, and
confirm their members-store role/team are set; confirm no-match grants nothing; confirm the projection updates on
login and (where the IdP enumerates) on a scheduled refresh.

**Acceptance Scenarios**:

1. **Given** a per-workspace role→group and team→group mapping, **When** a user signs in with matching IdP
   groups, **Then** their members-store role and team for that workspace are set/updated to the mapped values.
2. **Given** a user whose groups match no mapping, **When** the sync runs, **Then** they get no role/team
   (fail-closed default); no live per-request group→role resolution path exists.
3. **Given** an IdP that enumerates members, **When** a scheduled refresh runs, **Then** the projection updates;
   **Given** one that does not, **When** users log in, **Then** the projection is upserted from observed logins
   and marked best-effort (not an authorization source of record).
4. **Given** a non-name mapping reference (object GUID or SAML attribute value), **When** resolution runs,
   **Then** it works without assuming a group-name prefix convention.

### Edge Cases

- A user's IdP groups change between logins → resolved role/team reflect the latest login; the projection must
  not grant access it no longer should (it is not an authZ source anyway).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-021**: Sync a user's workspace roles/teams **into the members store** (the authority under RLS) from their
  IdP groups via a per-workspace mapping, without assuming a vendor group-naming convention (name/GUID/attr).
  Role/team authority MUST NOT be resolved live from IdP group claims per request.
- **FR-022**: A user whose groups match no mapping gets no role/team (fail-closed default) after the sync.
- **FR-023**: Maintain a best-effort `group_members` projection for notification fan-out (from enumeration where
  supported, else observed logins); it MUST NOT be an authorization source of record.

### Key Entities

- **Workspace IdP binding** (`workspaces.idp_binding`, existing jsonb): per-workspace role/team → native group
  ref (name/GUID/attr). Consumed, not changed.
- **Group-membership projection** (`group_members`, new): cache-only `(group_ref, subject, source, seen_at)`.
- **Members store** (existing `members`): the sync's write target; reads (`role_for`/`teams_for`) unchanged.

## Success Criteria *(mandatory)*

- **SC-007**: Role/team sync from IdP groups into the members store is correct for every mapping case tested
  (name/GUID/attr) and grants nothing when no mapping matches; no live per-request group→role path exists.

## Assumptions

- Create `0008_group_members` migration.
- Source: `docs/backend/18 §3–4`.
