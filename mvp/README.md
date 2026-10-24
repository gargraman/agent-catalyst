# agent-catalyst viability MVP

Throwaway, isolated from `frontend/` and the (nonexistent) `services/`. Its *findings* are the
deliverable, not the code. Full plan: `~/.claude/plans/quirky-jumping-graham.md`.

## Phase 1 (this commit): pure, no-model-call core + unit tests

Built here — all stdlib, no model calls:

- `ir.py` — `NormalizedRequest` / `NormalizedUsage` / `Substitution` (mirror `docs/backend/02`).
- `profiles/loader.py` + `profiles/*.json` — `CapabilityProfile` + `resolve_profile()`.
- `cost.py` — `compute_cost()` copied exactly from `docs/backend/02` §4.1.
- `evals/deterministic.py` — Layer-1 checks → `DetResult`.
- `evals/drift.py` — `verdict()`, `score_quality()`, `efficiency_delta()`, `EvalPolicy`, `DRIFT_TRIGGERS`.
- `log.py` — per-call JSONL logger.
- `tests/` — pytest for cost, verdict ordering, deterministic checks, quality scoring.

NOT built yet (later phases, require model calls): providers/adapters, the LLM judge, baseline,
experiments (H1/H2), calibration, scorecard.

## ⚠️ Capability profiles are PLACEHOLDERS

**Model lineup (locked 2026-10-22; OpenAI key + local Ollama).** Hosted pricing is seeded from
`frontend/src/lib/data.ts`; the exact OpenAI api model-id string is confirmed at first call (Phase 2).

| file | role | provider/model | input $/Mtok | output $/Mtok | notes |
|---|---|---|---|---|---|
| `openai__gpt-5-mini.json` | **runtime A** (cheap hosted; H1 baseline) | openai / gpt-5-mini | 0.6 | 2.4 | cached 0.06, cache_write 0.75, reasoning 2.4, batch ×0.5 |
| `ollama__llama3.1-8b.json` | **runtime B** (local; H2 + H1 downgrade) | ollama / llama3.1:8b | 0.0 | 0.0 | local, all pricing 0 (downloaded) |
| `ollama__llama3.2-3b.json` | H1 downgrade (weaker) | ollama / llama3.2:3b | 0.0 | 0.0 | local, all pricing 0 (downloaded) |
| `openai__gpt-5.json` | **judge** (`judge-v1`) | openai / gpt-5 | 5.0 | 20.0 | distinct from + more capable than runtime (mvp-spec §4) |

Hosted cached_input/cache_write/reasoning/batch are plausible-but-unverified derivations — confirm real
per-Mtok rates before trusting Phase-2 cost reconciliation. **Ollama is $0, so the ±5% hand-calc check only
validates the cost LOGIC on the OpenAI side; the cross-vendor cost-accounting divergence (review H5) is NOT
exercised until a paid 2nd vendor lands.**

## Setup & test

```bash
cd mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```
