# 11 — Deployment, Scaling, CI/CD & DR

> Implements `backend-integration-plan.md` Part 3 (deployment) + Part 4 (multi-tenant ops) + Part 5
> (cross-cutting). Turns the three processes from `00-overview.md §2` (`api`, `worker`, `web`) plus the
> stateful backing services into something you can `docker-compose up` today and lift to Kubernetes
> later **as config, not a rewrite**. Everything is self-hosted OSS — no cloud-managed-service lock-in.

> ⚠️ **Deploy safety (non-negotiable, from the operator's standing rules).** Never deploy or push to a
> prod or default branch (`main`/`master`) without explicit, in-session human confirmation. Several
> repos here **auto-deploy on push** (e.g. `main`→prod, `dev`→QA) — so a `git push` to such a branch
> *is* a production deploy. CI may build and push images to the internal registry automatically, but the
> **prod rollout stage is always manual-gated** (§6). Treat "merge" and "deploy to prod" as two
> separate, separately-authorized actions.

## 1. Topology at a glance

```mermaid
flowchart TB
  U[Builder UI] --> PROXY[Reverse proxy<br/>TLS · SSE pass-through]
  EXT[External callers / cURL] --> PROXY
  PROXY --> WEB[web<br/>Next.js standalone]
  PROXY --> API[api<br/>FastAPI · builder + published invoke]
  subgraph App[App tier — stateless, scale-out]
    API
    WRK[worker<br/>eval · ingest]
  end
  API --> PG[(Postgres<br/>+ pgvector)]
  API --> REDIS[(Redis<br/>queue · semaphores · cache)]
  API --> MINIO[(MinIO<br/>uploads · payloads)]
  WRK --> PG
  WRK --> REDIS
  API -. dispatch per-call .-> SBX[sandbox pool<br/>MCP + tool exec<br/>egress-restricted]
  WRK -. dispatch per-call .-> SBX
  SBX -. only egress .-> EGRESS[egress allow-list proxy<br/>Squid · deny-by-default]
  SBX -. decrypt cred .-> VAULT
  EGRESS -. allow-list .-> PROV
  EGRESS -. allow-list .-> INT[approved internal/external endpoints]
  API -. decrypt key .-> VAULT[(Vault<br/>per-workspace DEK)]
  WRK -. decrypt key .-> VAULT
  API --> LF[(Langfuse<br/>traces · cost)]
  WRK --> LF
  API --> PROV[LLM providers]
  WRK --> PROV
```

The **sandbox pool** (`SBX`, P2 — §2, §12) runs MCP servers + tool execution under a hardened isolation
runtime; it is reachable **only via `api`/`worker` dispatch** (never the proxy) and its **only network
path out is the egress allow-list proxy** (`config.egress`, 01 §4.6 / 09 §10) — deny-by-default, so a
compromised tool cannot exfiltrate or SSRF.

Legend: **App tier** (`api`, `worker`, `web`) is stateless and scales horizontally. **Postgres is the
stateful core.** Redis is queue + concurrency semaphores + cache (07). The `runtime` library (00 §3)
is the single code path shared by `api` and `worker`, so playground, eval, and published invoke run
identical code (00 §1).

| Unit | Image | Replicas (start) | Scales on | Stateless |
|---|---|---|---|---|
| `web` | `agent-catalyst/web` | 2 | request load | yes |
| `api` | `agent-catalyst/api` | 2 | request concurrency (HPA) | yes |
| `worker` (interactive) | `agent-catalyst/worker` | 1–2 | — (fast jobs) | yes |
| `worker` (batch) | `agent-catalyst/worker` | 0→N | queue depth (KEDA) | yes |
| `sandbox` (MCP + tool exec, P2) | `agent-catalyst/sandbox` | 0→N | per-call jobs (queue depth / KEDA) | yes · ephemeral per call · hardened isolation runtime (gVisor §12) |
| Postgres+pgvector | `pgvector/pgvector:pg16` | 1 (+replica later) | vertical + read replicas | **no** |
| Redis | `redis:7` | 1 | vertical | **no** |
| Vault | `hashicorp/vault` | 1 (HA: 3 raft) | — | **no** |
| Langfuse | `langfuse/langfuse` | 1 | — | **no** |
| MinIO | `minio/minio` | 1 (HA: 4) | storage | **no** |

> **Why split `worker` into interactive vs batch pools** (plan Part 4): eval + ingestion are bursty and
> CPU/token-heavy. A single pool lets a 120-case eval run starve a user's file ingestion. Two pools with
> separate queues keep latency-sensitive jobs responsive while batch autoscales by depth.

## 2. Images (Dockerfiles)

All images: multi-stage, slim base, **non-root user**, no secrets baked in (secrets come from Vault at
boot — §5). Pin digests in prod.

**`deploy/api.Dockerfile`** (uvicorn; shared by builder + published invoke):

```dockerfile
# --- build ---
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 POETRY_VIRTUALENVS_IN_PROJECT=true
WORKDIR /app
RUN pip install poetry
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY services/ services/
COPY migrations/ migrations/
RUN poetry install --only main            # install the local package

# --- runtime ---
FROM python:3.12-slim AS runtime
RUN useradd -m -u 10001 app && apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1
# uvloop + httptools; workers tuned per CPU. SSE needs --timeout-keep-alive high.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--timeout-keep-alive", "75"]
```

**`deploy/worker.Dockerfile`** — same build stage; different entrypoint. One image, pool chosen by env:

```dockerfile
FROM agent-catalyst/api AS base
USER app
# POOL=interactive|batch selects the queues this process consumes (§1).
CMD ["arq", "services.worker.main.WorkerSettings"]
```

**`web`** — Next.js **standalone** output (smallest runtime; this is a non-stock Next.js, see
`frontend/AGENTS.md`):

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build                          # next.config: output: "standalone"
FROM node:20-slim AS runtime
RUN useradd -m -u 10002 web
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER web
EXPOSE 3000
CMD ["node", "server.js"]
```

**`deploy/sandbox.Dockerfile`** (P2 — §1, §12) — the per-call MCP/tool-execution image. Same slim,
non-root base; it carries **only** the **MCP client (official `mcp` SDK) + the tool executor** and nothing
that needs broad reach. **No secrets are baked and none are loaded at boot** — vaulted provider/tool/
connector creds are injected **per call, into memory only** (§5, 09), and the container is torn down after
the call. It runs under the **hardened isolation runtime** (gVisor `runsc`) with **deny-by-default egress**
(§1, §12), dispatched by `api`/`worker` and never exposed to the proxy.

## 3. Dev: one `docker-compose up`

Brings up every service with seed data. Dev uses Vault in `-dev` mode (in-memory, auto-unsealed) and a
root token — **dev only**; prod uses AppRole + sealed storage (§5, 09).

```yaml
# deploy/docker-compose.yml  (dev)
x-app-env: &app-env
  AC_ENV: dev
  AC_DATABASE_URL: postgresql+psycopg://app:app@postgres:5432/catalyst
  AC_REDIS_URL: redis://redis:6379/0
  AC_VAULT_ADDR: http://vault:8200
  AC_VAULT_ROLE: dev
  AC_LANGFUSE_HOST: http://langfuse:3000
  AC_MINIO_ENDPOINT: http://minio:9000
  AC_OIDC_ISSUER: ${OIDC_ISSUER}            # from a gitignored .env (never committed)

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_USER: app, POSTGRES_PASSWORD: app, POSTGRES_DB: catalyst }
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck: { test: ["CMD-SHELL","pg_isready -U app"], interval: 5s, retries: 10 }

  redis:
    image: redis:7
    healthcheck: { test: ["CMD","redis-cli","ping"], interval: 5s, retries: 10 }

  vault:
    image: hashicorp/vault
    cap_add: ["IPC_LOCK"]
    environment: { VAULT_DEV_ROOT_TOKEN_ID: dev-root, VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200 }
    # dev mode auto-enables Transit; a bootstrap script creates the per-workspace keys (09).

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment: { MINIO_ROOT_USER: minio, MINIO_ROOT_PASSWORD: minio123 }
    volumes: ["miniodata:/data"]

  langfuse:
    image: langfuse/langfuse
    environment: { DATABASE_URL: postgresql://app:app@postgres:5432/langfuse, NEXTAUTH_SECRET: dev }
    depends_on: { postgres: { condition: service_healthy } }

  migrate:                                   # runs Alembic then exits (01 §7)
    image: agent-catalyst/api
    command: ["alembic", "upgrade", "head"]
    environment: *app-env
    depends_on: { postgres: { condition: service_healthy } }

  seed:                                      # loads frontend/src/lib/data.ts fixtures (01 §7, M1)
    image: agent-catalyst/api
    command: ["python", "-m", "services.api.seed"]
    environment: *app-env
    depends_on: { migrate: { condition: service_completed_successfully } }

  api:
    image: agent-catalyst/api
    environment: *app-env
    ports: ["8000:8000"]
    depends_on:
      seed: { condition: service_completed_successfully }
      redis: { condition: service_healthy }

  worker:
    image: agent-catalyst/worker
    environment: { <<: *app-env, POOL: interactive }
    depends_on: { api: { condition: service_started } }

  web:
    image: agent-catalyst/web
    environment: { NEXT_PUBLIC_API_URL: http://localhost:8080/api }
    ports: ["3000:3000"]

  proxy:
    image: nginx:1.27
    volumes: ["./deploy/nginx.conf:/etc/nginx/nginx.conf:ro"]
    ports: ["8080:8080"]
    depends_on: [api, web]

volumes: { pgdata: {}, miniodata: {} }
```

`migrate` and `seed` are **run-once** containers (gate the app on their completion) — this keeps schema
+ fixtures deterministic across every developer's machine.

## 4. Networking & the SSE problem

`web` and `api` sit behind **one reverse proxy** at the same origin — `web` serves the app, `/api/*`
routes to the FastAPI backend. Same origin kills CORS and lets the Next.js rewrite proxy stream.

> ⚠️ **SSE is the one hard part of the proxy (plan Part 2).** Streaming playground + published invoke
> use `text/event-stream`. Default proxy buffering **breaks streaming** — the client gets the whole
> response at once (or times out) instead of token-by-token. You must disable buffering and raise read
> timeouts on the `/api` + `/v1` paths. Test the full proxy path early (plan M2), not just the app.

```nginx
# deploy/nginx.conf  (SSE-safe)
worker_processes auto;
events { worker_connections 4096; }
http {
  map $http_upgrade $connection_upgrade { default upgrade; '' close; }
  server {
    listen 8080;
    # --- streaming API: buffering OFF, long timeouts ---
    location ~ ^/(api|v1)/ {
      proxy_pass http://api:8000;
      proxy_http_version 1.1;
      proxy_set_header Connection '';
      proxy_buffering off;                 # <-- the critical line for SSE
      proxy_cache off;
      proxy_read_timeout 3600s;            # long tool-loop / eval streams
      proxy_set_header X-Accel-Buffering no;
      chunked_transfer_encoding on;
    }
    # --- the app ---
    location / { proxy_pass http://web:3000; proxy_http_version 1.1; }
  }
}
```

The API also sets `X-Accel-Buffering: no` on SSE responses as a second line of defense (works even if an
intermediate proxy forgets). Traefik equivalent: `buffering` middleware off + `responseForwarding
flushInterval: -1ms`.

## 5. Config & secrets boot sequence

Config is env-driven (`00 §6`, `pydantic-settings`). **Secrets never live in images or committed env
files** — only in Vault (09). Services authenticate to Vault with **AppRole** (not a static token).

```mermaid
flowchart TD
  S1["container start"] --> S2["entrypoint: fetch Vault token via AppRole<br/>role_id from env · secret_id from mounted file / k8s auth"]
  S2 --> S3["load Settings (00 §6)<br/>infra URLs from env · nothing secret"]
  S3 --> S4["open DB pool · Redis · Langfuse clients"]
  S4 --> S5["readiness probe flips green → proxy routes traffic"]
```

Provider/tool/source API keys are **not** loaded at boot — they are fetched + decrypted **per invoke,
into memory only** (09), so a leaked process image or env dump exposes no provider credentials. The
`.env` file is gitignored; CI injects secrets from the platform secret store, never from the repo.

## 6. CI/CD — with the eval gate

Pipeline: **build → test (two layers) → eval gate → push images → deploy QA on green → prod manual-gated.**

```yaml
# .ci/pipeline.yml  (sketch — adapt to the org's CI)
stages: [build, test, eval_gate, publish, deploy_qa, deploy_prod]

build:
  script:
    - docker build -f deploy/api.Dockerfile  -t $REG/api:$SHA .
    - docker build -f deploy/worker.Dockerfile -t $REG/worker:$SHA .
    - docker build -f deploy/web.Dockerfile  -t $REG/web:$SHA .

test:                                        # layer 1: deterministic software tests
  script:
    - pytest services/ -q                     # unit + integration (incl. tenant-isolation suite, 09)
    - npm --prefix frontend test
    - python -m services.api.openapi_export > openapi.json
    - npx openapi-typescript openapi.json --output frontend/src/lib/api/schema.d.ts
    - git diff --exit-code frontend/src/lib/api/   # FAIL if FE client drifted from OpenAPI (08)

eval_gate:                                    # layer 2: versioned-prompt AI eval vs baseline (05)
  script:
    - python -m services.evals.ci_gate --suite ci --fail-under-drift
  # A model-/prompt-/knowledge-affecting change MUST show a PASS drift report or the pipeline fails.
  # Uses Batch API + verdict cache so the gate is cheap (07).

publish:                                      # push to internal registry (Trellix Artifactory)
  when: on_success
  only: [main, dev]
  script:
    - docker push $REG/api:$SHA && docker push $REG/worker:$SHA && docker push $REG/web:$SHA

deploy_qa:                                    # dev branch → QA, automatic on green
  when: on_success
  only: [dev]
  script: ["helm upgrade catalyst ./deploy/helm -f values.qa.yaml --set image.tag=$SHA"]

deploy_prod:                                  # main → prod, MANUAL approval required
  when: manual                                # <-- never automatic
  only: [main]
  script: ["helm upgrade catalyst ./deploy/helm -f values.prod.yaml --set image.tag=$SHA"]
```

> ⚠️ **Two safety rules encoded above.** (1) `deploy_prod` is `when: manual` — a human must click it in
> this session with explicit confirmation; CI never rolls prod on its own. (2) If this repo is wired to
> **auto-deploy on push** to `main`/`dev`, then opening/merging a PR to those branches is itself the
> deploy action — so branch protection + required review + the eval gate are the real controls, and you
> must confirm before pushing. The eval gate stage is what makes "merge" safe; it is not optional for
> model-affecting changes.

The OpenAPI → typed FE client diff (`test` stage) is what keeps frontend and backend types in lockstep
(08) — a backend contract change that isn't reflected in the generated client fails CI.

## 7. Prod: Compose-on-VM now, k8s-ready

> **Recommendation: start on Docker Compose on a hardened VM.** For an internal tool at this scale it's
> the simplest thing that works, and the design is cloud-agnostic so the later lift to Kubernetes is
> **config, not a rewrite.** Move to k8s when you need HA, autoscaling, or zero-downtime rollouts the VM
> can't give you — not before.

**Compose-on-VM** adds to the dev compose: real Vault (sealed, AppRole), TLS at the proxy
(Let's Encrypt / internal CA), external volumes on durable disk, log shipping, and
`restart: unless-stopped`. One VM, vertical scale, nightly backups (§9).

**Kubernetes + Helm** (when scale demands it):

```yaml
# deploy/helm/values.prod.yaml  (sketch)
image: { repo: artifactory.internal/agent-catalyst, tag: "" }   # tag set by CI (--set image.tag)

api:
  replicas: 3
  resources: { requests: { cpu: "500m", memory: 1Gi }, limits: { cpu: "2", memory: 2Gi } }
  hpa: { minReplicas: 3, maxReplicas: 20, targetCPUUtilizationPercentage: 65 }
  env: { AC_ENV: prod }

worker:
  interactive: { replicas: 2 }
  batch:                                      # autoscaled by Redis queue depth via KEDA
    minReplicas: 0
    maxReplicas: 30
    keda: { trigger: redis, listName: "queue:batch", listLength: "20" }

postgres:
  mode: operator                              # CloudNativePG / Zalando operator; or managed-in-cluster
  pgBouncer: { enabled: true, poolMode: transaction }   # required for RLS SET LOCAL correctness (01 §6)
  storage: 200Gi
  replicas: 1
  readReplicas: 1                             # dashboards/metrics read from replica

redis:   { ha: true }
vault:   { ha: { raft: { replicas: 3 } } }
minio:   { mode: distributed, replicas: 4 }
```

```yaml
# k8s Deployment skeleton (api) — probes + rolling update
spec:
  strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }
  template:
    spec:
      securityContext: { runAsNonRoot: true, runAsUser: 10001 }
      containers:
        - name: api
          image: artifactory.internal/agent-catalyst/api:{{ .Values.image.tag }}
          readinessProbe: { httpGet: { path: /readyz, port: 8000 }, initialDelaySeconds: 5 }
          livenessProbe:  { httpGet: { path: /healthz, port: 8000 }, periodSeconds: 15 }
          # serviceAccount bound to Vault k8s auth → AppRole-equivalent, no static secret (§5, 09)
```

> ⚠️ **PgBouncer + RLS:** the tenant guard sets `SET LOCAL app.workspace_id` inside the request
> transaction (01 §6, 09). This is **only safe in `transaction` pool mode** — `session` pooling would
> leak the GUC across tenants on a reused connection. This is a correctness-and-security constraint, not
> a tuning preference.

**Scaling shape** (plan Part 4): `api` scales out on request concurrency; `worker` batch pool scales on
queue depth; Postgres is the vertical/stateful core (read replicas for dashboards); Redis is queue +
semaphores (07) + cache. No app process holds tenant state in memory, so scale-out is safe.

## 8. Zero-downtime changes

| Change type | Rule |
|---|---|
| Schema | **Expand/contract only** (01 §7): add nullable → backfill → enforce → (later) drop. Never rename-in-place on a live table. |
| Deploy | Rolling update, `maxUnavailable: 0`; readiness-gated; old + new run together briefly, so migrations must be backward-compatible with the previous image. |
| API contract | Versioned under `/v1`; additive changes only within a version; breaking changes = new version + deprecation window; the OpenAPI/FE-client CI diff (§6) catches accidental breaks. |
| SSE | Long-lived streams: drain on shutdown (`terminationGracePeriodSeconds` ≥ max stream) so a rollout doesn't cut a user's in-flight generation. |

## 9. Telemetry store & retention

Split by shape (plan Part 5, cross-ref 01 + 10):

- **Transactional entities** (`agents`, `versions`, `runs`, `spans`, `eval_runs`) → Postgres.
- **High-volume trace/cost payloads** (full prompt/response bodies) → **Langfuse** (10); ClickHouse
  later if trace volume outgrows it.
- `runs` and `spans` are high-write → **partition by month** and apply a retention policy so the primary
  DB doesn't bloat. Large eval/trace payloads go to **MinIO** keyed from the row.

```sql
-- runs partitioned monthly (01 defines the table; this is the prod partitioning)
create table runs (...) partition by range (created_at);
create table runs_2026_10 partition of runs for values from ('2026-10-01') to ('2026-11-01');
-- a scheduled job pre-creates next month's partition and drops partitions past retention.
```

Per-workspace retention + PII redaction before any log/trace write are security requirements (09, 10).

## 10. Backup & disaster recovery

> Backups you have never restored are not backups. Schedule **restore drills**, don't just schedule
> dumps.

| Asset | Method | RPO | RTO | Runbook |
|---|---|---|---|---|
| Postgres (all transactional data + pgvector) | **PITR**: base backup + WAL archiving (pgBackRest / wal-g) to object storage | ≤ 5 min | ≤ 1 h | restore base + replay WAL to timestamp; re-point app |
| pgvector indexes | Rebuilt from `chunks` rows; HNSW `REINDEX` after a restore if needed | — | ≤ 2 h (large KBs) | reindex runbook; retrieval degraded-but-serving during rebuild |
| Vault (DEKs — **loss = all tenant keys unrecoverable**) | Raft snapshot backup; **unseal keys / recovery keys in separate custody** | ≤ 1 h | ≤ 1 h | restore snapshot → unseal with quorum of recovery keys |
| MinIO (uploads, large payloads) | Bucket replication / versioning + periodic snapshot | ≤ 15 min | ≤ 1 h | re-point endpoint; re-ingest only if objects lost |
| Langfuse (traces/cost) | Its Postgres is backed by the PITR above; non-critical to serving | ≤ 1 h | best-effort | observability data; not on the serving critical path |
| Secrets config (AppRole, OIDC) | In the platform secret store, IaC-managed | — | ≤ 30 min | re-provision from IaC |

> ⚠️ **Vault is the highest-stakes asset.** Provider/tool/source keys are envelope-encrypted with
> per-workspace DEKs held in Vault (09). Lose Vault *and* its unseal/recovery keys and every tenant's
> encrypted credentials are permanently unrecoverable (users would have to re-enter all provider keys).
> Keep recovery-key custody split across people, back up raft snapshots off-box, and drill the unseal.

## 11. Environments & promotion

| | dev | qa | prod |
|---|---|---|---|
| Vault | `-dev` (in-mem, root token) | sealed, AppRole | sealed HA raft, AppRole + split recovery keys |
| Data | seeded from `data.ts` (01 §7) | anonymized sample | real tenant data |
| Deploy | `docker-compose up` | auto on green push to `dev` (QA) | **manual-gated** from `main` (§6) |
| Scale | single container each | 1–2 replicas | HPA + KEDA (§7) |
| Providers | cheap models + mocks (07) | real, small budget | real, per-workspace budgets (07) |

Promotion flow: PR → CI (build + two-layer tests + eval gate) → merge to `dev` → **auto QA** → soak +
eval report green → promote image tag to **prod via manual approval** with explicit confirmation.

## 12. Dependency management, upgrade policy & LangGraph licensing

The runtime sits on two fast-churning libraries — **LangGraph** (orchestration) and **LiteLLM**
(transport) — whose breaking changes can silently alter behavior or cost. This is managed, not hoped:

- **Exact pinning.** `poetry.lock` is committed and authoritative; prod images pin by **digest** (§2). No
  floating ranges on LangGraph/LiteLLM/pgvector client — exact versions only.
- **Deliberate upgrades, gated by eval.** A LangGraph/LiteLLM bump is its own PR, never bundled. Because
  these libraries affect model behavior and cost accounting, the **two-layer eval suite is the upgrade
  gate**: the bump must pass the provider-abstraction contract tests (same `NormalizedRequest` across
  providers, cost matches hand-calc — 02, spec §D) **and** a PASS drift report vs baseline (05, §6
  `eval_gate`) before merge. A green unit build is necessary but not sufficient for an LLM-lib upgrade.
- **Breaking-change runbook.** On a major bump: read the changelog for IR-affecting changes (streaming
  event shape, tool-call format, usage fields), update the adapter/profile layer (02) if needed, re-run
  the contract + drift gates, and record any capability-profile deltas. If the lib changes cost-relevant
  behavior, reconcile (02 §4.1) before promoting.
- **License scan in CI.** A dependency license scan runs in CI (the internal dependency-check tooling);
  a newly-pulled Denied/Review license blocks the build (ties to the §6 pipeline).
- **MCP client + sandbox runtime are pinned the same way** (P2, §1/§2). The MCP/tool-exec path adds two
  more load-bearing deps, **exact-pinned and re-verified on every bump** exactly like LangGraph/LiteLLM/
  pgvector:
  - **MCP client library** — the official **Model Context Protocol Python SDK** (`mcp` package,
    `modelcontextprotocol/python-sdk`, **MIT**). Provides `ClientSession` + the stdio
    (`mcp.client.stdio`) and Streamable-HTTP/SSE (`mcp.client.streamable_http`/`sse`) transports that
    back `mcp_servers.transport ∈ {stdio, http}` (01). A protocol/transport change can silently alter
    discovery/invoke — treat a bump like a gateway bump.
  - **Sandbox runtime** — **gVisor (`runsc`)** container runtime (**Apache-2.0**) + deny-by-default
    egress via **k8s NetworkPolicy (Cilium/Calico)** + a forward **allow-list proxy (Squid)** as the only
    route out (§1). **Kata Containers / Firecracker microVM** is the documented stronger-isolation
    escalation path if gVisor's syscall surface proves insufficient.
  - Both are added to the **CI license scan** above: MCP SDK **MIT**, gVisor **Apache-2.0** — both
    permissive, re-checked on bump.
- These two pins are what make **`config.egress` (01 §4.6 / 09 §10) a real network boundary** rather than
  an advisory field: the Squid allow-list proxy + NetworkPolicy enforce the per-server egress allow-list,
  and the **same machinery backs the provider-egress precondition** for key-custody enforcement
  (09 §9.1 / 14 §1).

**Resolved — spec risk #7 (LangGraph Platform licensing).** **Decision (2026-10-19): use the OSS
LangGraph *library* (MIT) behind our own FastAPI; do NOT adopt LangGraph *Platform*** (the managed
deploy/mgmt product with its separately-tiered license). This keeps the stack portable, self-hostable,
and free of a new per-seat/usage license on a compounding bill — consistent with the "no cloud-managed
lock-in" principle (§intro) and the build-vs-buy story (`scope.md`). LiteLLM is MIT. Re-verify both
licenses at each major bump via the CI license scan above.

---

This completes the deployment + ops picture; monitoring/alerting/SLOs are in `10-observability-monitoring.md`,
and the security controls referenced throughout (Vault envelope, RLS, egress sandbox) are in
`09-security-tenancy.md`. See `README.md` for how all the backend docs fit together.
