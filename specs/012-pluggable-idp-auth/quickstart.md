# Quickstart / Validation — Pluggable IdP Bearer-Token Auth (feature 012)

Validation guide (not implementation). Commands run from `services/` unless noted.

## Prerequisites

- Compose Postgres up (existing): `docker compose -f deploy/compose.yaml up -d` (host port 5433, healthy).
- venv: `cd services && .venv/bin/python -m pytest` is the test entrypoint.
- New dep installed: `pyjwt[crypto]` (added to `pyproject.toml [api]`).

## 1. Offline automated suite (the real guarantee — no network, no secrets)

```bash
cd services
.venv/bin/python -m pytest -q \
  tests/test_auth_jwt_provider.py \
  tests/test_auth_registry.py \
  tests/test_api_auth_bearer.py \
  tests/test_api_auth_membership.py
```

Expected:
- JWT provider unit tests pass using a **locally-generated RSA keypair** (no call to Trellix IAM, no secret).
- The 401 matrix (bad sig / wrong aud / wrong iss / expired / nbf / `alg=none` / HS256 / unknown kid / missing)
  all reject with `401` and leak no token.
- Membership tests (live Postgres) resolve the role from `members` under RLS; non-member → `403`;
  cross-tenant → `403`.

## 2. Full regression + purity (nothing else breaks)

```bash
cd services
.venv/bin/python -m pytest -q          # all suites; existing ~423 stay green (dev provider preserved)
.venv/bin/python -m pytest -q tests/test_runtime_purity.py tests/test_evals_purity.py
```

Expected: full suite green (new tests added); purity green (no auth/jwt/http imports in `runtime/`/`evals/`).

## 3. Provider-swap proof (SC-003)

With config `AC_AUTH_PROVIDER=dev` the suite authenticates via `X-Dev-Principal`; flipping a test to
`AC_AUTH_PROVIDER=trellix-iam` (local JWKS injected) accepts signed JWTs instead — **with no edit to any router
or `require_permission`**. Demonstrated by `test_auth_registry.py` + diff review.

## 4. Production-gate proof (FR-008)

`test_auth_registry.py` asserts: `AC_AUTH_PROVIDER=dev` + `AC_ENV=prod` → fails closed (dev provider refused).

## 5. Opt-in LIVE Trellix-IAM check (manual; NOT in CI — FR-015)

Reads client credentials from gitignored `mvp/.env`; obtains a real token; submits it to a locally-running API
configured with `AC_AUTH_PROVIDER=trellix-iam`.

```bash
# from repo root, with the API running locally against compose Postgres and a seeded membership row
python3 scripts/iam_live_check.py mvp/.env     # prints: token verified -> Principal{subject, scopes}; request authN OK
```

Expected: the live token verifies against the live `jwks_uri`; a seeded membership for `sub` (client_id) in the
target workspace yields the role; the request authenticates. Evidence recorded in the completion report; this
step is excluded from the default `pytest` run.

## Done / acceptance

- SC-001..SC-002: 401 matrix + valid-token success (step 1).
- SC-003: provider swap, zero call-site edits (step 3).
- SC-004: offline + secret-free suite (step 1/2).
- SC-005: purity green (step 2).
- SC-006: existing RBAC/tenancy/spend tests green (step 2).
- SC-007: documented live-IAM check (step 5).
