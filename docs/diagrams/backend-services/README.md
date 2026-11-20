# Backend services — diagram set

Four self-contained HTML diagrams of the designed (not-yet-built) `services/` backend, covering the
four themes requested: **service interaction, scaling, security, and per-service components.**

Built with the `diagram-design` skill. Palette matches the existing archify set in
`../backend-design/` (classic-light: emerald services, violet stores, rose security, amber cloud,
cyan HTTP edge). Each file is standalone — open it in a browser; no build step.

| File | Theme | What it shows | Source docs |
|---|---|---|---|
| [`service-interaction.html`](service-interaction.html) | **Interaction** | The load-bearing idea — four thin callers (playground, published, eval, channel) converge on one `runtime.invoke()` core; pipeline (guardrails → retrieval → gateway) streams to providers, decrypts from Vault, traces to Langfuse. | `backend/00 §1`, 02, 04, 06, 10 |
| [`scaling-model.html`](scaling-model.html) | **Scaling** | Stateless app tier (web, api, two worker pools, sandbox) scales **out** on request concurrency / queue depth (HPA · KEDA); stateful core (Postgres, Vault, Langfuse/ClickHouse, Redis, MinIO) scales **up** / HA. | `backend/11 §1`, §7 |
| [`security-tenancy.html`](security-tenancy.html) | **Security** | Secure paved road across three trust zones — permitted ingress through the auth guard, guard + RLS, Vault envelope, the privileged `tool.approve` gate, the gVisor sandbox, and two **blocked** paths (direct→sandbox, open egress). | `backend/09 §1–§12`, 11 §1 |
| [`service-components.html`](service-components.html) | **Components** | Major modules inside each unit — api (routers · auth+tenancy · publish), the shared `runtime/` library (invoke+graph · **gateway** · guardrails+retrieval · obs+cache), evals, ingestion, sandbox. | `backend/00 §3` |

## Relationship to `../backend-design/`

That folder (archify engine) documents **lifecycles and flows** — published-invoke sequence, eval-drift
gate, RAG, deployment plane, observability, etc. This folder (diagram-design engine) gives the
**four cross-cutting structural views** of the services themselves. The two sets share a palette and
are meant to sit side by side.

## Regenerate / verify

```bash
# static contract check (accessible-SVG, single-file safety)
python3 ~/.claude/skills/diagram-design/scripts/self_check.py <file>.html

# visual check (macOS)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --disable-gpu --screenshot=out.png --window-size=1300,1100 "file://$PWD/<file>.html"
```
