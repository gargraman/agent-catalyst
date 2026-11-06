# Tenancy & Security Requirements Checklist: M4-remainder — Resource CRUD + Frontend Sync

**Purpose**: Validate that tenant-isolation, no-secret, authorization, and fail-closed *requirements* are complete,
clear, and consistent before implementation (P5-focused). These test the spec, not the code.
**Created**: 2026-11-05
**Feature**: [spec.md](../spec.md)

**Note**: Reviewer-owned. `[x]` means the reviewer judged the requirements-quality criterion satisfied — NOT that
implementation is done. `/speckit-implement` reads but never changes these markers.

## Requirement Completeness

- [x] CHK001 - Are workspace-isolation requirements defined for EVERY new table (tools, mcp_servers, mcp_tools, skills, workspace_settings, members, runs)? [Completeness, Spec §FR-016]
- [x] CHK002 - Are negative cross-tenant acceptance criteria specified per new table, not just one representative? [Coverage, Spec §FR-016, SC-002]
- [x] CHK003 - Is the server-side authorization requirement (caller claim → active workspace) specified for the new admin/members endpoints? [Completeness, Spec §FR-016]
- [x] CHK004 - Is the requirement that the privileged/owner DB role never serves these requests stated? [Completeness, plan Constitution P5]
- [x] CHK005 - Are data-classification and retention/deletion requirements stated for the new tables holding PII (members) and operational data (runs)? [Gap, Constitution P5]

## Requirement Clarity

- [x] CHK006 - Is fail-closed behavior on unset/invalid workspace scope specified as returning zero rows (not an error that leaks existence)? [Clarity, Spec §FR-016]
- [x] CHK007 - Is "no secret value persisted or returned" defined with explicit criteria for what counts as a secret within tool `http_config` / MCP auth metadata? [Ambiguity, Spec §FR-021]
- [x] CHK008 - Is the "console role stored but not enforced" requirement unambiguous about which endpoints explicitly do NOT gate on role in this feature? [Clarity, Spec §FR-012]
- [x] CHK009 - Are the per-failure error codes (malformed id → input.invalid; foreign/absent id → not_found; stale ETag → conflict.stale) specified rather than left to implementation? [Clarity, Spec §FR-020]

## Requirement Consistency

- [x] CHK010 - Is the workspace-root (create/delete) out-of-scope boundary stated consistently across spec, plan, and contracts? [Consistency, plan gap #2]
- [x] CHK011 - Are the deferred secret-bearing resources (provider-keys, tokens, tool-approval) consistently marked as NOT persisting secrets here? [Consistency, Spec §FR-021, Assumptions]

## Edge Case & Coverage

- [x] CHK012 - Does the spec define how a secret-looking field submitted to a tool is handled (rejected vs silently dropped)? [Edge Case, Gap, Spec §FR-021]
- [x] CHK013 - Are requirements defined so not-found/cross-tenant responses do not reveal that another tenant's resource exists? [Edge Case, Gap]
- [x] CHK014 - Is it specified that archived rows remain subject to tenant isolation (archive does not bypass RLS)? [Edge Case, Spec §FR-003/016]
- [x] CHK015 - Are requirements defined for minimizing member PII (email/display_name) in responses, logs, and the tracker? [Gap, Constitution P5]

## Acceptance Criteria Quality

- [x] CHK016 - Is the "no secret persisted/returned" outcome specified as objectively verifiable (secret-scan / absence test)? [Measurability, Spec §SC-008]
- [x] CHK017 - Can each tenant-isolation requirement be verified by a named negative cross-tenant test? [Measurability, Spec §SC-002]

## Traceability

- [x] CHK018 - Is each tenancy/security acceptance criterion linked to a specific FR and its verifying test (ID scheme)? [Traceability]
