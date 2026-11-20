# Quickstart & Verification — Provider-key custody (feature 014)

Runnable validation for the slice. All commands run from `services/` with the project venv. The default suite is hermetic (no cloud, no container beyond Postgres); the AWS path is opt-in against LocalStack.

## Prerequisites
```bash
# dev stack (Postgres/Redis already used by the suite); LocalStack only needed for the opt-in AWS test
docker compose -f deploy/compose.yaml up -d postgres redis            # (+ localstack for the opt-in test)
cd services && .venv/bin/python -m alembic upgrade head               # applies migration 0007
```

## Named verification commands (map to Success Criteria)

| What | Command | Proves |
|---|---|---|
| Secrets provider unit (local roundtrip, masking, errors) | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_secrets_provider.py` | FR-001/002/007/008; SC-002 |
| **P6 purity** — runtime imports no boto3/api.secrets | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_runtime_purity.py` | FR-015 |
| Provider-keys API (capture/list/rotate/revoke, RBAC, no-plaintext) | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api_provider_keys.py` | FR-004/006/009/010/011/012/017; SC-001/002/007 |
| Resolve-at-invoke/eval + fail-closed + cross-tenant | `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_api_provider_key_resolve.py` | FR-013/014/016; SC-003/004/005 |
| Full regression | `.venv/bin/python -m pytest -p no:cacheprovider -q` | no regressions across 013's 533 tests |
| Secret-scan (no plaintext leaked) | the CI `secret-scan` job over the diff | SC-001 |
| **Opt-in** AWS ⇄ LocalStack roundtrip | `AC_RUN_LOCALSTACK=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_aws_secrets_localstack.py` | FR-002/019; SC-006 |

## End-to-end scenarios (expected outcomes)

1. **Capture is write-once / masked-only** — `POST /api/v1/provider-keys` with a raw value as an admin → `201` with `{masked, status:active}` and **no** `value`/`vault_path`; the stored row holds a masked value + opaque ref (asserted: plaintext never in the response or the `provider_keys` row). `GET` returns masked only.
2. **RBAC** — a non-`key.manage` caller gets `403` on capture/list/rotate/revoke.
3. **Tenancy** — workspace A cannot see (list), resolve, rotate, or revoke workspace B's key (`404`/absent); negative cross-tenant tests pass (SC-005).
4. **Resolve-at-invoke** — with an active key stored for the agent's provider, invoking the agent uses the **stored** key (not `PROVIDER_API_KEY`); an eval-run resolves the same way.
5. **Fail-closed** — with no active key for the provider (none, or the only one revoked), invoke/eval returns a `config_error` and makes **zero** provider calls (mocked gateway asserts no call).
6. **Rotate** — after rotate, a subsequent run uses the new secret; the old secret no longer resolves. `rotatedAt` updates; id/label unchanged.
7. **Revoke** — after revoke, the row is retained as `revoked` and resolution fails closed; the backend secret is disabled.
8. **Backend swap (SC-006)** — the same API/resolve tests pass against `AC_SECRETS_PROVIDER=local` (default suite) and against `AC_SECRETS_PROVIDER=aws` + LocalStack (opt-in), with **no** call-site code change.

## Paid-call protocol
No live paid provider call is introduced as a test. Resolution is verified with stored **test** values and a mocked provider gateway; the LocalStack path uses no real cloud. If anyone later runs a real invoke against a live provider with a stored key, the existing paid-call protocol (approved model, synthetic input, spend ceiling, retained evidence) applies.

## Docs (DoD, docs-only — link/consistency/render checks, not unit tests)
- `docs/scope.md` — add the decision-log entry: Vault Transit → pluggable secrets / AWS Secrets Manager (why: AWS deployment target; kept swappable).
- Full Vault→AWS sweep across `docs/backend/**` + `docs/*.md` + diagram sources; `docs/backend-integration-status.md` provider-keys row → BUILT; refresh the seam count (253→257).
