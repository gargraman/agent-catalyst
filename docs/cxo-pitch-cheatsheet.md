# Agent Catalyst — CxO Pitch Cheat Sheet

**Audience:** CTO + Chief Architect (technical) · **Length:** 10 min · **Ask-first if rushed.**

## Remember just this: **1 number · 3 gaps · 1 engine · 1 ask**

---

## The 1 number (your hook)
**Bedrock spend is up 20%/month.**
→ ~2× a quarter · ~3× in 6 months · **~9× in a year.**
Don't just say it's big — say it's **unseen, uncapped, and can't be safely shrunk.**

## The 3 gaps (the real problem)
Every team hand-wires agents; nobody owns the lifecycle. So the bill has:
1. **No visibility** — can't say what drives spend.
2. **No safe cheaper lever** — can't move to a cheaper model without gambling on quality.
3. **No cap** — spend per team/agent is unbounded.
*(Plus: when a model updates, nobody checks the agent didn't silently get worse.)*

## The 1 engine (the winning insight)
> **The same eval/drift core answers "is it still good?" AND "can it be cheaper?"**
> One hard thing, two governance wins. That's why it's **not** a FinOps dashboard and **not** a router.

## The 1 ask
> **"2 engineers, 8 weeks, to prove the eval/drift core against our real Bedrock traffic — then a go/no-go."**
> Build-time cost ~$100/mo. If it fails, we killed a bad bet cheap.

---

## Slide spine (8 slides, ~8:30)
| # | Slide | One line to say |
|---|---|---|
| 1 | **Curve** | "20% a month → 9× in a year. Unseen, uncapped, can't shrink safely." |
| 2 | **3 gaps** | "Same root cause: nobody owns the agent lifecycle." |
| 3 | **Idea** | "Control plane for AI agents: one runtime, governed, eval-proven." |
| 4 | **Architecture** | "Test, prod, eval all call the same runtime on an immutable snapshot → verdicts are trustworthy." |
| 5 | **Cost lever** | "See → safely downgrade → options → caps. I won't promise a bill cut — I promise it's governed." |
| 6 | **Build vs buy** | "Buy the plumbing, build only the gate. 8 weeks tells us how much." |
| 7 | **Case + ask** | Formula, not a fake total → the one-sentence ask. |
| 8 | **Close** | "9× in a year, unseen. I want it seen, capped, safely cheaper." |

## Cost math (say the formula, never a fake total)
**Saving ≈ (share safely movable) × (price gap) × (current bill)** — and it **compounds**.
Illustrative: 40% on premium × 50% cheaper = ~20% of bill → buys back ~1 month of growth, every month.

---

## Earn the technical room (Slide 4 depth)
- **Three callers, one core:** playground + published endpoint + eval all call the *same* `runtime.invoke`.
- **Immutable VersionSnapshot:** eval tests the *exact bytes prod runs*, not a lookalike.
- **Cost from our own price profiles**, not the provider's reported number.

## Build-vs-buy (own it before they ask)
"Reuse LiteLLM/Langfuse/OSS for routing + tracing. **Nobody sells the enforced gate** — can't publish or downgrade unless eval proves it's safe, tied to our IAM, no new per-call SaaS on a compounding bill. If buying covers the gate, I'll buy it."

## Quick Q&A
- **Bedrock Agents?** "Governs Bedrock only; native features lock us in tighter."
- **Just LiteLLM + Langfuse?** "Those are parts we reuse. The product is the *enforced lifecycle*."
- **'Model-agnostic' leaks?** "Yes — that's why we recompute cost + re-run eval on every swap. Gate catches the leak."
- **How much is just adoption?** "Probably most — so I pitch governance + a safe lever, not a cut."
- **Who maintains it / distraction?** [Have your ownership answer ready — this is the CTO's real worry.]

---

## 🚨 Landmines (don't)
1. **Don't promise a $ bill cut** to the people who own the bill — give the *formula*, ask them to validate the model mix.
2. **Don't imply the backend works** — the console is a *prototype of the experience*; the runtime + eval core are the ask.
3. **Don't present the eval core as built** — it's "designed, ready to build."

## 3 takeaways to leave them with
1. AI spend is compounding, unseen, uncapped.
2. One engine makes "cheaper" and "still good" **provable**.
3. Cheap, time-boxed ask to prove the hard part.

## Before the room
☐ Fill the ask sentence (people + weeks) ☐ Get real Bedrock $ + model mix ☐ Rehearse build-vs-buy most ☐ One-page leave-behind ☐ Timed run-through under 8:30
