# Agent Platform × AI Champions Playbook — alignment & claims backbone

> Source of truth for the ELT video. Every line in `demo-transcript.md` and `elt-deck.html` must trace to
> a row here, and must not overstate a **Status**. Audience framing: a Leader presents this to the ELT.
>
> **Positioning rule:** Agent Platform **aligns with** the AI Champions program — it is **not part of it**,
> and we are **not selling the program**. Never imply ownership ("backbone *of* the program", "system of
> record *for the program*"). Say: *the playbook sets the rules; Agent Platform is how they get enforced.*
>
> Naming: the product is **"Agent Platform"** (`frontend/src/lib/org.ts` → `ORG.productName`); the prior
> decks called it **"AI Agent Control Plane."** This doc uses "Agent Platform," positioned as *the control
> plane for AI agents*. Pick one name before recording — see Open items.

## The one idea

The **AI Champions Playbook is a set of rules in a PDF** — register every agent, reuse first, flag
overlaps, gate on evals, cap cost/tokens, least-privilege, "an agent is an API." **Today nothing enforces
them.** Reuse is eyeballing; "no agent without registration" is an honor system; drift goes unnoticed until
a customer complains.

**Agent Platform aligns directly with that playbook — it is the governance layer that makes those rules
actually stick.** The control plane every agent passes through to production: if it isn't registered,
evaluated, and scoped, it doesn't ship. Not another agent, and not the program itself — **how the program's
principles get enforced in software.**

## What the ELT should feel (stakes → outcomes)

- **Risk:** ~40 agents shipping with no enforcement = security/compliance exposure, and **silent quality
  drift that reaches customers** (real examples below).
- **Cost:** duplicated agents nobody caught; unbounded per-agent spend.
- **Speed:** every team re-solves registration, eval, access, publishing by hand.
- **Outcomes Agent Platform buys:** **risk down** (enforced eval + least-privilege), **cost down** (kill
  duplicates, admin-set budgets + token caps + allowed models), **speed up** (safe shipping from one console).

## Playbook → Agent Platform capability map

Status legend: **mock** = clickable in the console today (seed data / in-memory) · **designed** = specified
in `docs/` + `docs/backend/`, not built · **net-new** = not in code or docs yet.

| Playbook rule / DoD item | Agent Platform capability | Demo surface | Status |
|---|---|---|---|
| #2 Reuse first · flag overlapping agents · consolidation | Agents list + filters; **similarity scanner at registration** (dedup) | `/agents`; scanner narrated | list **mock**; **scanner net-new** |
| DoD "**Registered** w/ owner"; #1 sponsor-scoped intake | Workspace = agentic area; ownership + access policy; **create-time registration gate** | `/agents/new`; gate narrated | policy **mock**; **gate net-new** |
| #5 "An agent is an **API**"; DoD "versioned REST + OpenAPI" | Publish → versioned endpoint + cURL / TS / Python export | `/agents/[id]` → Publish & Export | **mock** |
| #4/#10 model **justified by evals**; re-eval on every change | **Eval/drift core + publish gate (409 without a passing eval)** | `/evaluations`; Publish tab | **designed** (defensible core) |
| #4/#6 right-size model; **cost budget + token caps**; model configurable | **RBAC admin** sets per-agent budget, token caps, allowed models; cost/capability profiles | role switcher; `/agents/[id]` Configure | **mock** |
| #7/#8 **least-privilege**; roles; federate-don't-forward | Roles `viewer<builder<publisher<admin` (`permissions.ts`); `<Gate>`; runtime-access tokens | "Preview as role" switcher; `/playground` | **mock** |
| reach from existing surfaces (chat apps) | **Channels** (Slack / Teams / Google Chat), Okta-federated, two access gates | `/channels` | **mock** |
| #3 **LangChain/LangGraph** standard; one shared runtime | Designed orchestration = LangGraph; one `runtime.invoke` for playground/eval/endpoint/channel | architecture (backup) | **designed** |
| Lifecycle "operate & improve": metrics, drift | Dashboard + Runs + drift badges | `/dashboard`, `/runs` | **mock** |
| #6 security review + SDL + scanning | **Not Agent Platform's job** — it's the gate/registry that *records* sign-off | — | out of scope |

## Real, honest proof points (use these, don't invent)

- **Drift is real in the seed:** `Incident Report Writer` went **eval_failing** — quality **−9.2** on a
  **model swap to gpt-5**; `Resume Screener` **−11.3** on a **prompt edit**. Both blocked from publish.
  (`frontend/src/lib/data.ts`)
- **Duplication is visible:** a new "SOC Alert Triage Assistant" would collide with the published **SOC
  Triage Copilot** — the scanner's whole point.
- **RBAC is real + demoable:** roles `viewer/builder/publisher/admin`; admin adds `access.manage` +
  `key.manage`; "Preview as role" switcher gates the UI live. (`frontend/src/lib/permissions.ts`)
- **Channels:** one install → one workspace → many agents; identity federated from Okta; two gates
  (channel Okta group + per-agent policy). Seed: SOC Slack, People Ops Google Chat, Compliance Teams.
- **Agentic area = workspace:** XDR, GRC, Labs, People Ops exist as tenants (`frontend/src/lib/iam.ts`).

## Three landmines (handle in every artifact)

1. **"Aligns with," not "part of."** No ownership language about the program (see Positioning rule).
2. **Bedrock → Vertex:** no "Bedrock spend +20%/mo" lead; any cost mention is Vertex/GCP or neutral.
3. **Prototype honesty:** "a **working prototype of the full experience**, plus a **time-boxed investment in
   the one hard part — the eval/drift core**." Never "just a mock." Registration gate + scanner are
   *proposed*, shown as mock.

## The CTA (alignment framing, compact — folded into the close)

- Agent Platform **aligns with** the AI Champions Playbook — it's how those principles get enforced.
- **Adopt it** as the place agents are registered, proven, and governed.
- **Prove it on one area first** (e.g. Zak's — DevOps + Debugging).
- One-line orchestrator nod: Champions run the whole lifecycle from one console instead of hand-wiring glue.

## Open items (resolve before recording)

- **Product name:** "Agent Platform" vs "AI Agent Control Plane" (prior decks). Pick one.
- **Reel order:** Kanan first, Raman second (confirmed) — Raman's opener bridges from Kanan's topic (TBD at 1:1).
- Deconflict vs existing governance-flavored projects (SAGE, ETP Atlas, Boris's orchestrator): our unique
  claim is *platform / registry / eval-gate*, not another point agent.
