# 11 (LLD) — Deployment: process boundaries, the shared DB layer, boot & migrations

> **Parent:** [`../11-deployment.md`](../11-deployment.md) (topology, Dockerfiles, compose, nginx/SSE,
> CI/CD + eval gate, k8s/Helm, backup/DR, dependency policy).
> The parent is the ops manual. It is complete on *what runs where* but leaves four buildable seams that
> every service process depends on and that no single doc owns: the **shared DB engine/pool** (`00-conv §4.1`
> and `09 §1` both *use* `SessionLocal`/`create_async_engine` but neither defines it), the **Vault AppRole
> boot** (prose in `11 §5`), the **Alembic runner + enum/JSONB evolution** (`01 §7` is a bullet list), and the
> **lifespan + `/readyz`** implementation (`10 §4` names the probe, `11 §7` the k8s wiring). This is the
> focused delta — not a second ops manual.

## What this adds over parent `11`

| Gap in `11` (and siblings) | Filled here |
|---|---|
| `00-conv §4.1` / `09 §1` reference `SessionLocal = async_sessionmaker(bind=async_engine, …)` but **no doc builds the engine** — driver, pool size, the PgBouncer-transaction-mode connect arg | §2 — the one `db.py` engine every process imports; the PgBouncer-mode gotcha; how `api`/`worker` share it + the runtime library |
| `11 §5` Vault AppRole boot + per-call decrypt is a mermaid sketch; `09 §9` builds the envelope but not the **boot login** | §3 — env-vs-Vault split, the AppRole login at startup, where the `09` Transit/DEK config is wired |
| `01 §7` migrations = 6 bullets; **enum `ADD VALUE` is non-transactional** and `profile_snapshot` grew fields (`16 §3.1`, A1) — neither has a buildable pattern | §4 — the Alembic runner, the autocommit-block enum migration, the JSONB forward-compat rule |
| `10 §4` names `/healthz` `/readyz`; `11 §5` sketches boot order — no `lifespan`, no readyz check code, no shutdown drain impl | §5 — `lifespan`, the `/readyz` check, SIGTERM drain for in-flight SSE |
| `11 §7` scaling is prose (HPA/KEDA); the **DB connection budget** across `api`×replicas × uvicorn workers + worker pools is undefined | §6 — the pool-math formula + the worker concurrency knob |

Python 3.12, `from __future__ import annotations`, SQLAlchemy **`AsyncSession`** (asyncpg) — the session model is
pinned in [`00-conventions.lld.md §4`](00-conventions.lld.md). Snippets compile (§8).

---

## 1. Process boundaries (what each of the three images actually imports)

`00 §2/§3` and `11 §1` fix three stateless app processes over the stateful backing services. The load-bearing
rule for *code layout* is **one `runtime/` library, imported by two callers**:

| Process | Entrypoint (`11 §2`) | Imports | Does **not** import |
|---|---|---|---|
| `api` | `uvicorn services.api.main:app` | `services.api.*` (routers, `db.py` §2, `settings.py`), **`runtime/`** (`invoke`, 03) | arq worker functions |
| `worker` | `arq services.worker.main.WorkerSettings` | `services.worker.*` (jobs), **`runtime/`**, `db.py` §2 | FastAPI app / routers |
| `web` | `node server.js` (Next standalone) | nothing Python — talks to `api` over `/api` (`11 §4`) | the DB, Vault, Redis |

`runtime/` is a **pure library, no FastAPI/HTTP imports** (`00 §3`), so the *same* `invoke()` runs under `api`
(playground + published invoke), under `worker` (eval cases, `05`), and — via the worker — the channel path
(`12`). That single code path is the whole reason drift reports evaluate the code the endpoint runs (`00 §1`);
deployment must never fork it (e.g. a worker-only shim around `invoke`). `web` holds **no tenant state** (`11 §7`)
— all identity/tenancy is resolved server-side per request (`09`), so every process scales out freely.

---

## 2. The shared DB layer (`db.py`) — the engine `00-conv §4.1` and `09 §1` assume

Both `00-conv §4.1` (session pinned) and `09 §1` (`get_db` dependency, `set_tenant_guc`) build on a
`SessionLocal`/`async_engine` that **no doc constructs**. It lives in one module imported by `api` and `worker`:

```python
# services/api/db.py — the single engine + sessionmaker (consumed by 09 §1 get_db / job_tenant)
from __future__ import annotations
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from services.api.settings import settings            # 00 §6

# Driver is asyncpg (00-conv §4.1, normative). DSN: postgresql+asyncpg://…
engine = create_async_engine(
    str(settings.database_url),
    pool_size=10, max_overflow=5,                      # per PROCESS; connection budget in §6
    pool_pre_ping=True, pool_recycle=1800,             # drop connections a pooler/Postgres may have killed
    connect_args={"statement_cache_size": 0},          # REQUIRED behind PgBouncer `transaction` mode (§6, 11 §7)
)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
```

- **PgBouncer transaction mode is mandatory for RLS** (`11 §7`, `09 §7`): the tenant GUC is `SET LOCAL
  app.workspace_id` inside the request transaction, so the connection must be checked out **per transaction**.
  `session` pooling would leak the GUC across tenants. The asyncpg-specific cost: server-side prepared
  statements don't survive the pooler's connection reuse, so `statement_cache_size=0` is not a tuning knob — it
  is a correctness requirement with PgBouncer `transaction`.
- **`api`** opens one `AsyncSession` per request via `get_db` (`09 §1`), in a transaction so `SET LOCAL` resets
  at commit. **`worker`** opens one per job via `job_tenant` (`09 §1.2`) for the same reset guarantee. Neither
  opens a sync `Session` on the event loop (`00-conv §4.1`).
- **The worker builds its own engine** from the same module at `on_startup` (separate process → separate pool);
  it does **not** share `api`'s pool object, only the construction code.

> ⚠️ **Inconsistency flagged (resolved: standardized on asyncpg).** `11 §3` dev compose previously set
> `AC_DATABASE_URL: postgresql+psycopg://…`, but `00-conv §4.1` pins the **asyncpg** driver. The DSN scheme now
> reads `postgresql+asyncpg://…` (folded into `11 §3`) so the `connect_args` above (`statement_cache_size=0`)
> apply. (`+psycopg` is psycopg3, also async, but then the pooler guard would be `prepare_threshold=None`, not
> `statement_cache_size`.) One driver, chosen once.

---

## 3. Config & secrets wiring (env vs Vault; the AppRole boot)

`00 §6` fixes `Settings` (`pydantic-settings`, `AC_` prefix) as **boot-time infra wiring + safe defaults only —
no secrets**. `11 §5` sketches the boot; here is the split and the login.

| Comes from **env** (`Settings`, `00 §6`) | Comes from **Vault** (`09 §9`) |
|---|---|
| `AC_DATABASE_URL`, `AC_REDIS_URL`, `AC_VAULT_ADDR`, `AC_VAULT_ROLE`, `AC_LANGFUSE_HOST`, `AC_MINIO_ENDPOINT` | DB password / Redis auth (if not in the DSN), the session-signing secret |
| `AC_OIDC_ISSUER`, `AC_OIDC_CLIENT_ID` | **Provider / tool / connector / channel API keys** — never at boot; decrypted **per call, in memory** (`09 §9`, `11 §5`) |
| tunable defaults (budget, judge model/version, cache toggles — `00 §6`) | the **Transit DEK keys** (`platform-pool`, `ws-<id>`, `user-<hash>` — `09 §9.1` / `09-security.lld §3`) |

```python
# services/api/vault.py — AppRole login at startup (11 §5 step S2; 09 §9 "AppRole-authed at boot")
from __future__ import annotations

async def vault_login(settings) -> "VaultClient":
    """role_id from env (AC_VAULT_ROLE), secret_id from a mounted file / k8s auth (NEVER in the image, 11 §2).
    Returns a client holding a renewable token; the token's validity is a /readyz check (§5)."""
    role_id = settings.vault_role
    secret_id = _read_secret_id()                      # file mount (prod) or k8s serviceaccount auth (11 §7)
    client = VaultClient(str(settings.vault_addr))
    await client.auth_approle(role_id, secret_id)      # dev uses -dev root token instead (11 §3)
    return client
```

The `VaultEnvelope` / `KeyVault` that this client backs — the three DEK scopes, the datakey envelope, rotation —
is built in [`09-security-tenancy.lld.md §3`](09-security-tenancy.lld.md); deployment only *constructs the
authenticated client at boot* and holds it on `app.state` for the per-call just-in-time decrypt. A leaked
process image or env dump therefore exposes **no** provider credentials (`11 §5`).

---

## 4. Migrations on deploy (Alembic runner + the two evolutions `01 §7` leaves open)

Migrations run as the **`migrate` run-once container** (`11 §3`): `alembic upgrade head`, gated to completion
before `api`/`seed` start. `/readyz` also asserts **migrations are at head** (§5) so a half-migrated schema never
takes traffic. Rollout is **expand/contract only** (`01 §7`, `11 §8`) so the previous image stays compatible
during a rolling update.

```python
# migrations/env.py — async engine; extensions BEFORE any table (01 §7)
from __future__ import annotations
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

def run_migrations_online() -> None:
    engine = create_async_engine(str(settings.database_url), poolclass=pool.NullPool)  # one-shot, no pooling
    async def _run() -> None:
        async with engine.connect() as conn:
            await conn.execute(_text("create extension if not exists vector"))
            await conn.execute(_text("create extension if not exists pgcrypto"))   # gen_random_uuid (01 §7)
            await conn.run_sync(_do_migrations)        # context.configure(...) + context.run_migrations()
    asyncio.run(_run())
```

### 4.1 Enum evolution — `ADD VALUE` is non-transactional

Postgres `ALTER TYPE … ADD VALUE` **cannot run inside the transaction** Alembic wraps each migration in, and the
new value **cannot be used in the same transaction** that adds it. The enums in `01 §3` already grew this way —
`notification_kind` gained `approval_requested`, `job_kind` gained `model_deprecation_scan` — so this is the
buildable pattern, not a hypothetical:

```python
# migrations/versions/xxxx_extend_enums.py
from alembic import op

def upgrade() -> None:
    with op.get_context().autocommit_block():          # escape Alembic's per-migration transaction
        op.execute("ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'approval_requested'")
        op.execute("ALTER TYPE job_kind         ADD VALUE IF NOT EXISTS 'model_deprecation_scan'")
    # RULE: never consume the new value in THIS migration (no backfill using it here). A later migration,
    # running in its own transaction, may reference it. IF NOT EXISTS makes re-runs idempotent.
```

Enum values are **append-only** in practice — removing one is a drop+recreate+rewrite of every dependent column,
which expand/contract forbids on a live table. Deprecated values stay (see the `ingest_status` tombstone, `01 §3`).

### 4.2 `profile_snapshot` shape change — JSONB, no DDL

`agent_versions.profile_snapshot` is **JSONB** (`01 §4.2`), so the fields that grew it — `availability` /
`sunset_at` (`16 §3.1`, owned by `02 §4`) and the A1 `fallback` sub-object (`02 §4/§6`) — need **no column
migration**. Old frozen snapshots simply lack the keys (and by design never change — immutability, `02 §4`).
Forward-compat is a **read-time default in code**, not an Alembic step:

```python
# reading a (possibly old) snapshot — defaults make missing keys safe (02 §8 / 16 §3.1 / A1)
availability = snap.get("availability", "active")      # pre-16 snapshots: treat as active
sunset_at    = snap.get("sunset_at")                   # None
fallback     = snap.get("fallback", {})                # pre-A1 snapshots: no fallback pinned
```

The lesson for deployment: **JSONB schema growth is a code concern (default on read); only typed columns and
enums are Alembic concerns.** A backfill of old snapshots is explicitly *not* done — they are immutable.

---

## 5. Startup ordering, health/readiness & drain

`10 §4` defines `/healthz` (liveness: process up) and `/readyz` (readiness: Postgres + Redis + Vault token +
migrations-at-head). `11 §5` fixes the boot order. Here they are built:

```python
# services/api/main.py — lifespan wires boot order (11 §5), probes implement 10 §4
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.vault = await vault_login(settings)      # S2: Vault token via AppRole (§3)
    app.state.redis = await open_redis(str(settings.redis_url))
    # engine (§2) connects lazily on first checkout; Langfuse client is fire-and-forget (10)
    yield                                              # ← readiness flips green; proxy routes traffic
    await app.state.redis.aclose()                     # SIGTERM: see drain note below
    await engine.dispose()

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}                             # liveness: no dependency touched

@app.get("/readyz")
async def readyz(request) -> dict:
    checks = {
        "db": await _ping_db(),                         # SELECT 1 on the engine
        "redis": bool(await request.app.state.redis.ping()),
        "vault": await _vault_token_valid(request.app.state.vault),   # token lookup-self
        "migrations": await _alembic_at_head(),         # §4: current revision == head
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)   # infra probe → raw 503, NOT the 08 envelope
    return {"status": "ready", **checks}
```

- **`/readyz` deliberately bypasses the `08` error envelope** (`00-conv §1`): a 503 here is for k8s/the proxy,
  not an API client. It is the one sanctioned non-`AppError` error path.
- **Migrations-at-head is a readiness gate, not a crash:** a pod on an old schema reports *not ready* and takes
  no traffic (rather than crash-looping), so a rolling deploy where `migrate` hasn't finished degrades safely.
- **SSE drain on shutdown (`11 §8`):** on SIGTERM, stop accepting new connections, let in-flight streaming
  `invoke()` generations finish within `terminationGracePeriodSeconds` (≥ the 30s run deadline, `03 §4`), then
  exit — so a rollout never cuts a user's live generation. `engine.dispose()` runs after drain.

---

## 6. Scaling knobs & the connection budget

`11 §7` gives the autoscale *shape* (api on request concurrency via HPA; worker-batch on queue depth via KEDA;
Postgres vertical). The missing number is the **DB connection budget**, which the pool in §2 makes concrete:

```
conns(api)    = api_replicas × uvicorn_workers × (pool_size + max_overflow)
              = 3 × 4 × (10 + 5) = 180            # 11 §2 Dockerfile: --workers 4; 11 §7: 3 replicas
conns(worker) = worker_procs × (pool_size + max_overflow)
total must stay under Postgres max_connections — OR be multiplexed by PgBouncer (transaction mode, §2/11 §7).
```

PgBouncer in transaction mode is what makes this safe: the app-side pools can stay small because the pooler
multiplexes many short tenant-scoped transactions onto few server connections. **Knobs:**

| Knob | Where | Effect |
|---|---|---|
| `uvicorn --workers` | `api.Dockerfile` (`11 §2`) | CPU parallelism per `api` pod; multiplies the connection budget above |
| `pool_size` / `max_overflow` | `db.py` (§2) | per-process ceiling on concurrent DB work |
| arq `max_jobs` (`WORKER_CONCURRENCY`) | `worker` env | concurrent jobs per worker process; interactive pool stays small, batch pool scales 0→N on queue depth (KEDA, `11 §7`) |
| `terminationGracePeriodSeconds` | k8s (`11 §8`) | must ≥ max SSE stream for clean drain (§5) |

**SSE caveat:** a streaming request holds an `api` worker slot for the whole generation but should hold a DB
*connection* only for the short `_open_run`/`_close_run` transactions (`00-conv §4.2`), never for the token loop
— otherwise long streams exhaust the pool. The `AsyncSession`-only rule (`00-conv §4.1`) is what keeps the loop
off a blocking connection.

---

## 7. Deployment-specific error & edge taxonomy

Only the cases the general taxonomy (`00-conv §1`) does **not** cover:

| Condition | Behavior | Why |
|---|---|---|
| `/readyz` dependency down | raw **503** (not `08` envelope), pod not routed | infra probe; k8s/proxy consumer, not an API client |
| migrations not at head | `/readyz` false → no traffic | avoids running against a half-initialized schema |
| Vault token expired/unrenewable | `/readyz` false; per-call decrypt would fail mid-run (`10 §4`) | refuse traffic rather than fail invokes after billing starts |
| SIGTERM mid-stream | drain window (§5), then exit | never cut a live generation (`11 §8`) |
| PgBouncer `session` mode (misconfig) | **tenant GUC leak** across tenants | §2/`09 §7` — a correctness/security failure, not perf; transaction mode is mandatory |

---

## 8. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `00`/`01`/`02` LLD); the
   Alembic/SQL fragments are `op.execute(...)` strings, not executed here.
2. **Cross-ref** — against `11` (topology, Dockerfiles `--workers 4`, compose, k8s `3 replicas`, `§8`
   zero-downtime drain), `00 §3/§6` (runtime library + `Settings`), `00-conv §4.1` (asyncpg/`AsyncSession`),
   `09 §1` (`get_db`/`job_tenant`/`SET LOCAL`), `09 §9` (Vault envelope + AppRole), `10 §4` (`/healthz`,
   `/readyz` definition), `01 §3/§4.2/§7` (enums, `profile_snapshot` JSONB, Alembic bullets).
3. **Found inconsistencies flagged, (a) now resolved:** `11 §3` compose DSN used `+psycopg` while
   `00-conv §4.1` pins `asyncpg` — **resolved: standardized on asyncpg** (`11 §3` DSN + the §2 callout); (b) none
   other — the enum/JSONB growth in `01 §3`/`16 §3.1`/A1 is consistent with the patterns in §4.
4. **No scope contradiction** — this is the "~90% plain software" tier (`scope.md`, `CLAUDE.md`): plumbing for
   the two defensible cores (runtime `02/03`, eval/drift `05`), introducing no model logic.
