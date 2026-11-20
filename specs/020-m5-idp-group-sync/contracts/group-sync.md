# Contract: 020 IdP group → members-store sync + projection

Feature-scoped contract. Authoritative shared contract: [015 contracts/console-auth.md §"Login-time group → members-store sync"](../../015-m5-security-completion/contracts/console-auth.md). This file records the **020 realization deltas**. There are **no new HTTP routes** — the sync is a write-side effect of 019's `/auth/callback`.

## Trigger (on 019's `/auth/callback`, after `issue_session`)

```
1. groups = active_provider.groups_for(subject)        # None when the IdP can't enumerate (trellix-iam → None)
2. if groups is None:  NO-OP, never revoke (D3)         # claim-less pilot → members unchanged
3. else for each workspace the subject touches:          # groups is AUTHORITATIVE
     resolve `groups` through workspaces.idp_binding (roles + teams; name/GUID/attr)
     if any mapping matches:
        UPSERT members(workspace_id, subject).console_role + .teams    # ON CONFLICT (workspace_id, subject)
     else if a member row already exists:
        CLEAR the synced role/team (D4 revoke — reconcile to the shrunk/empty mapping; keep the row)
     else:
        write NOTHING                                   # first-time no-match → fail-closed default (FR-022)
4. UPSERT group_members(workspace_id, group_ref, subject, source='login_seen', seen_at=now)   # projection only (FR-023)
```

## Scheduled refresh (callable; not wired to a scheduler yet — D5)

```
sync.refresh_group_projection(workspace_id):
  for group_ref in workspaces.idp_binding:
     members = active_provider.members_of(group_ref)    # None when unsupported → skip
     UPSERT group_members(..., source='members_of')     # projection only, never authZ
```

## Invariants (the reconciliation — research R-note)

- **No live per-request group→role path exists.** `require_permission`/access-policy keep reading the members store via `membership.role_for`/`teams_for`, unchanged (FR-021).
- `group_members` is **never** read to grant access — notification fan-out only (FR-023).
- A first-time no-mapping-match login grants **no** role/team (FR-022); an authoritative shrink **revokes** a prior synced role/team (D4); a `None` (can't-enumerate) result never revokes (D3).
- No vendor group-naming convention is assumed — refs are name / object-GUID / SAML-attribute (FR-021).

## Acceptance (maps to FR-021..023, SC-007)

1. Login with mapped IdP groups → members-store role/team upserted to match (name / GUID / attr cases).
2. Login with no mapping → no role/team granted.
3. `group_members` updated `source='login_seen'` on login; scheduled refresh updates `source='members_of'` where supported.
4. A claim-less provider (`groups_for → None`, trellix-iam) → sync is a no-op; login still succeeds.
5. The sync writes only within the tenant (RLS) session; no cross-tenant write.
