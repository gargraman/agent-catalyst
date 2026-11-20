# Contract — Provider-keys HTTP API (feature 014)

All routes under `/api/v1/provider-keys`, workspace-scoped (active workspace resolved as elsewhere), gated on the **`key.manage`** permission (admin) via the existing `require_permission` dependency, composing with membership + RLS. Error envelope + ETag/validation conventions reuse `api/errors.py` / existing patterns. **No raw secret is ever returned by any route.**

## Schemas (`api/schemas/provider_keys.py`)

```
ProviderKeyCreate:
  provider: str          # required, non-empty
  label:    str          # required, non-empty, unique per workspace
  value:    str          # required, non-empty — the RAW secret (write-once, never echoed)
  region:   str | None

ProviderKeyRotate:
  value:    str          # required, non-empty — the NEW raw secret

ProviderKeyOut:          # mirrors frontend ProviderKey (drop-in) — NO value, NO vault_path
  id:        str
  provider:  str
  label:     str
  masked:    str
  region:    str | None
  added:     datetime    # <- created_at
  rotatedAt: datetime | None   # <- rotated_at
  status:    "active" | "expiring" | "revoked"
```

## Endpoints

### `POST /api/v1/provider-keys`  (capture, write-once)
- **Perm**: `key.manage`. **Body**: `ProviderKeyCreate`.
- **Behavior**: validate (non-empty value/label/provider); write raw value to the configured secrets backend (`put`); compute `masked`; insert a `provider_keys` row (`status=active`, `vault_path` from backend). If the backend write fails → no row is persisted (atomic; `config_error`).
- **201** → `ProviderKeyOut` (masked only). **Never** returns `value`.
- **Errors**: `400 input.invalid` (empty value/label); `403 access.denied` (not `key.manage`); `409 conflict.*` (duplicate `(workspace,label)`); `5xx config_error` (backend unavailable, no partial row).

### `GET /api/v1/provider-keys`  (list, masked only)
- **Perm**: `key.manage`. **Query**: active-workspace scoped (RLS).
- **200** → `ProviderKeyOut[]` — masked values + metadata only; other workspaces' keys absent (tenancy).

### `POST /api/v1/provider-keys/{id}/rotate`
- **Perm**: `key.manage`. **Body**: `ProviderKeyRotate`.
- **Behavior**: replace the backend secret (`rotate`); update `masked` + `rotated_at`; keep `id`/`label`/`status=active`. Superseded value no longer resolves.
- **200** → `ProviderKeyOut`. **Errors**: `404 not_found` (unknown id / other workspace — no existence leak across tenants); `403`; `400`.

### `POST /api/v1/provider-keys/{id}/revoke`
- **Perm**: `key.manage`.
- **Behavior**: set `status=revoked` (tombstone, row retained); disable the backend secret (`disable`). Idempotent on an already-revoked key.
- **200** → `ProviderKeyOut` (`status=revoked`). **Errors**: `404`; `403`.

## Resolution (internal, not an HTTP route)
At **invoke** and **eval-run**, the server calls `resolve_provider_key(session, ws, provider)` after loading the version snapshot, and passes the plaintext into `RunContext.api_key`.
- **Success**: returns the plaintext of the most-recently-added `active` key for `(workspace, provider)`.
- **No active key / backend unavailable**: raise **`config_error`** before any provider call (fail-closed, FR-014). In **non-prod only** (`AC_ENV != prod`), fall back to `PROVIDER_API_KEY`.
- **Tenancy**: RLS guarantees only the caller's workspace rows are visible; cross-tenant resolution is impossible.

## Invariants (asserted by tests)
- No route or log line or error body ever contains the raw `value` or `vault_path`.
- `GET`/rotate/revoke on another workspace's key behaves as if it does not exist (404), never leaking existence.
- Non-`key.manage` callers are denied on every route (403).
