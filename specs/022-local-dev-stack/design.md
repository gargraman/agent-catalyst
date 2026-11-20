# Feature 022 — Local dev stack: MinIO object storage + one-command bring-up & integration tests

**Status:** BUILT · **Date:** 2026-11-13 · **Type:** dev-infra (no production app code)
**Depends on / amends:** `deploy/compose.yaml`, `docs/backend/11-deployment.md`, `docs/scope.md`

> ⚠️ **AS-BUILT REVERSAL (2026-11-13):** this design was written for **MinIO**, but MinIO was reversed to
> **LocalStack S3 (local) + AWS S3 (prod)** *during* the build when MinIO's community images turned out to be
> no longer anonymously pullable (removed from Docker Hub Sept 2026; quay.io 401s on anonymous pulls). The
> MinIO-specific detail in §§4–9 below is superseded — the authoritative as-built is the `docs/scope.md`
> 2026-11-13 decision-log entry, the `docs/backend/11` AS-BUILT note, and `quickstart.md`. Everything else
> (scope, depth, observability placeholder, test-gate selection, Makefile UX) stands as written.

---

## 1. Intent & why now

We want to actually **boot the backend stack locally and run its integration tests** — not just reason about
it on paper. Today the secret-custody half of that is already real: `deploy/compose.yaml` runs a `localstack`
container (Secrets Manager + KMS) and `api/secrets/aws_provider.py` drives the *same* boto3 path locally that
prod runs against real AWS. What's missing is **object storage** (uploads / payloads / RAG documents), which
the design (`docs/backend/11`) has always placed on **MinIO** — an S3-compatible, self-hosted store used both
locally and in prod — but which has never been added to the dev compose.

This feature adds MinIO to the local stack, bootstraps its buckets, gives the whole thing a **one-command
bring-up and a runnable integration-test target**, and lays the **shared substrate** for the deferred M11
observability stack (Langfuse v3 + ClickHouse) so turning it on later is additive, not a rewrite.

## 2. Decisions locked (by the user, this session)

1. ~~**Object store = MinIO**, locally *and* in prod.~~ **REVERSED (see banner): object store = AWS S3,
   emulated by LocalStack S3 locally.** LocalStack now runs `secretsmanager,kms,s3`. The original rationale
   (match prod, avoid drift) still holds — it's just satisfied by AWS S3 everywhere instead of MinIO, because
   MinIO's community images became unavailable and the deployment target is already AWS.
2. **Scope = secrets + object storage now, plus a *thin syntactic placeholder* for Langfuse + ClickHouse.**
   The observability services exist in compose behind a disabled profile, validated only for compose-syntax
   correctness (`docker compose --profile observability config`), and **explicitly documented as NOT
   Langfuse-v3-ready** — they will be rebuilt into the real v3 topology when observability is actually turned
   on. We are *not* building a drop-in v3 stack now (the builder-check flagged that the pre-v3 shape can't come
   up green; we accept the placeholder as a known, labelled stub rather than pull v3 work forward).
3. **Depth = infra only.** No application-side storage client, no `Settings` fields for object storage, no
   real upload feature. Just: containers, bucket bootstrap, developer UX, and runnable tests.
4. **Process = lightweight direct** — implement from this design doc; no full speckit spec/plan/tasks set.

## 3. Goals

- `make up` (or equivalent) brings up Postgres+pgvector, PgBouncer, Redis, LocalStack (secrets), **MinIO**,
  runs migrations, and starts the API — healthy, with buckets created.
- MinIO buckets are **bootstrapped automatically** on bring-up (idempotent): the app object-store bucket(s)
  and the `langfuse` event-upload bucket (substrate for observability).
- A single command runs the **opt-in integration tests** against the live stack: the existing
  `test_aws_secrets_localstack.py` (Secrets Manager) **plus** a new MinIO bucket/roundtrip infra test.
- The deferred **Langfuse v3 + ClickHouse** services exist in compose behind a disabled profile so
  `make observability-up` (later) is the only step needed — no compose surgery.
- Design docs reconciled to the as-built state (per the standing "update docs after feature" rule).

## 4. Non-goals / anti-goals

- **No app-side S3 client** in `services/` (no `api/storage/`, no `Settings.minio_*`, no boto3 S3 in product
  code). Object storage is wired as *infrastructure that's ready*, not *code that uses it*. The seam comes with
  the first real upload feature.
- **No change to the prod object-store choice** — it stays MinIO, as designed. We are not introducing AWS S3.
- **Observability not running by default.** Langfuse/ClickHouse are present-but-off (compose profile). Fully
  standing them up green is a follow-up, not this feature.
- **Not** adding MinIO to `/readyz` — readiness only tracks dependencies the request path actually needs, and
  no request path touches object storage yet (would be a false dependency). Revisit with the upload feature.
- **Anti-goal:** local and prod drifting onto different object stores (the reason we rejected "LocalStack S3
  local / MinIO prod"). MinIO both sides keeps the path identical.

## 5. What gets built (concrete deltas)

### 5.1 `deploy/compose.yaml`
- **Add `minio` service**: `minio/minio`, command `server /data --console-address ":9001"`, ports
  `9000:9000` (S3 API) + `9001:9001` (console), creds `MINIO_ROOT_USER=minio` / `MINIO_ROOT_PASSWORD=minio123`
  (dev-only dummy, flagged as never-a-real-cred), volume `miniodata:/data`, healthcheck hitting
  `/minio/health/ready`.
- **Add `minio-setup` one-shot** (`minio/mc`): waits for MinIO healthy, then idempotently creates buckets
  `ac-uploads` (app object store) and `langfuse` (observability substrate). Exits 0; nothing depends on it
  staying up. This is the bucket bootstrap.
- **`api` service env (documented-ready, not consumed yet):** add `AC_MINIO_ENDPOINT` /
  `AC_MINIO_ACCESS_KEY` / `AC_MINIO_SECRET_KEY` as **commented-out** lines with a `# infra-only: no Settings
  field reads these yet (feature 022)` note — so the wiring point is obvious when the storage seam lands,
  without implying a live dependency that doesn't exist. *(Resolved — comment-only, §8.)*
- **Observability placeholder (thin, profiled, OFF):** add `clickhouse` and `langfuse` service definitions
  guarded by `profiles: ["observability"]` so default `docker compose up` ignores them, plus the `chdata`
  volume. These are a **deliberately thin placeholder**, NOT a working Langfuse-v3 topology — each carries a
  clear `# PLACEHOLDER — not Langfuse-v3-ready; rebuilt when observability is turned on (feature 022 §2)`
  comment. **No** `langfuse` Postgres-DB bootstrap one-shot is added (unnecessary for a stub that never runs).
  **Acceptance = `docker compose --profile observability config` parses cleanly and default bring-up is
  unaffected** — nothing is brought up or smoke-tested. The MinIO `langfuse` bucket is still created now (cheap,
  harmless substrate).
- Update the file's header comment (currently "M11 … MinIO … added") to reflect MinIO now added here.

### 5.2 Developer UX — `Makefile` (repo root; new)
No task runner exists today. Add a thin `Makefile` delegating to `docker compose -f deploy/compose.yaml`:
- `make up` / `make down` / `make logs` — lifecycle.
- `make bootstrap` — (re)run bucket creation (usually automatic via `minio-setup`).
- `make test-integration` — ensure `localstack` + `minio` are up, then run the opt-in suite with
  `AC_RUN_LOCALSTACK=1` (and the MinIO env) → `pytest -m integration` / the gated tests.
- `make observability-up` — `docker compose --profile observability up -d` (turns on the deferred stack).
*(Make is my call as the neutral, dependency-free choice; a `scripts/dev-stack.sh` is the alternative — §8.)*

### 5.3 Tests
- **Keep** `test_aws_secrets_localstack.py` as-is (already gated by `AC_RUN_LOCALSTACK=1`).
- **Add** `services/tests/test_minio_buckets_localstack.py` — opt-in under the **same `AC_RUN_LOCALSTACK=1`
  gate** (one switch for "integration against the local emulated stack"). Using **boto3's S3 client test-side
  only** (boto3 is already an `api` dep; MinIO is S3-compatible, path-style + `us-east-1`), it asserts the
  bootstrapped buckets exist and does a `put_object`→`get_object`→`delete_object` roundtrip against
  `ac-uploads`. Verifies the *infrastructure* (bucket bootstrap + MinIO reachable) without a product-side client.
- **`make test-integration` selects by the env gate, NOT `pytest -m integration`.** The existing secrets test
  is unmarked and left as-is; selecting by marker would silently skip it and report a half-green "both tests
  pass". The target sets `AC_RUN_LOCALSTACK=1` and runs the gated tests so **both** actually execute.
  *(Resolved, §8 — fixes the builder-check's #3.)*

### 5.4 Docs (reconcile to as-built)
- `docs/backend/11-deployment.md` — update the "AS BUILT" note: dev compose now includes **MinIO** (object
  storage) alongside LocalStack (secrets); observability (Langfuse/ClickHouse) present behind a profile, off by
  default.
- `docs/scope.md` decision log — one entry: feature 022 local dev stack; MinIO added, LocalStack stays
  secrets-only, observability deferred-but-scaffolded.
- A short `specs/022-local-dev-stack/quickstart.md` — the 3-command local-run + test recipe.

## 6. How it hangs together (local topology)

```
docker compose up  ──>  postgres(pgvector)  ──(migrate one-shot)──>  api  :8000
                        pgbouncer (txn pool, app role)                 │
                        redis                                          │ boto3 → Secrets Manager
                        localstack (secretsmanager,kms)  :4566  <──────┘
                        minio (S3 API :9000 / console :9001)  <── minio-setup creates ac-uploads, langfuse
                        [profile: observability] clickhouse + langfuse   (present, not started)
```
`make test-integration` points the opt-in tests at `:4566` (secrets) and `:9000` (MinIO), proving both the
same-code-path secrets contract and the bucket bootstrap on the live emulated infra.

## 7. Verification plan

- `docker compose -f deploy/compose.yaml up` → all default services reach healthy; `minio-setup` exits 0;
  MinIO console reachable at `:9001`; buckets present.
- `make test-integration` → secrets roundtrip test + MinIO bucket/roundtrip test both **pass** against the
  live stack. (These are opt-in and remain skipped in the normal unit run — the unit suite stays infra-free.)
- Unit suite (`pytest` without the gate) unaffected — no new default-run imports, no boto3 pulled into the
  unit run. I will run it and report pass/fail honestly rather than assume.
- `docker compose --profile observability up` is **out of verification scope** (deferred), but the profile is
  syntactically valid so default bring-up is unaffected.

## 8. Resolved decisions (all forks closed)

- **Process:** lightweight direct — implement from this doc. ✅ (user)
- **Observability scaffold:** thin syntactic placeholder — profiled, OFF, labelled not-v3-ready, acceptance =
  `compose config` validity only. ✅ (user)
- **MinIO env on `api`:** comment-only + marker. ✅ (my call)
- **UX:** root `Makefile`. ✅ (my call)
- **Test gate:** single `AC_RUN_LOCALSTACK=1`; `make test-integration` selects by env gate, not marker, so both
  tests run. ✅ (my call; resolves builder-check #3)
- **Langfuse Postgres-DB bootstrap:** dropped — unnecessary for a placeholder that never runs. ✅ (my call;
  resolves builder-check #2 sub-point)
- Builder decides: MinIO image tag (pin to repo style), dev creds, boto3 path-style/region, whether `make up`
  blocks on `minio-setup`, host-side pytest. No architectural latitude beyond these.

## 9. What's left to the builder

Mechanical implementation of §5 following repo conventions (compose style, test gating pattern from
`test_aws_secrets_localstack.py`, doc "AS BUILT" note style). No architectural latitude — all forks are in §8.
