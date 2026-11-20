# Agent Catalyst viability experiment

This is a standalone CLI experiment for the provider-abstraction and eval/drift hypotheses. It is not the production `services/` backend. Its code and findings are exploratory; production modules still need to be built and verified under the implementation plan.

## Status (2026-10-31)

- **Phase 1 core:** normalized request/usage types, model profiles, cost calculation, deterministic checks, verdict/quality helpers, and per-call JSONL logging are implemented.
- **H2 harness:** a single normalized streaming request is exercised against OpenAI `gpt-5-mini` and local Ollama `llama3.1:8b`. Both adapters, usage normalization, a paid-provider cost check, and capability-substitution reporting are implemented. The check asserts the expected Ollama reasoning downgrade; it does not establish exhaustive validation of every possible downgrade. These are narrow experiment adapters, not the production gateway.
- **Observed live calls:** the local, gitignored `logs/h2.jsonl` currently has 8 records: 4 OpenAI and 4 Ollama. All 4 OpenAI records have non-empty output; 3 Ollama records have non-empty output and 1 has an empty output. The logger's `success` field means “output text was non-empty.” The log does not include token counts, substitutions, run IDs, or the CLI assertion/exit report, so it is not enough to claim that H2 passed.
- **H1 plumbing (offline):** `python -m run h1` now runs an **offline, mocked-judge** harness (`experiments/h1_eval.py`) that exercises the real deterministic + verdict pipeline across a control arm and three seeded-regression arms (correctness floor, quality drop, efficiency budget), reporting detection/false-positive rates. This is **engine-plumbing evidence only** — it proves the pipeline mechanically flags a seeded drop and passes an unchanged agent; it is **not** the H1 hypothesis result.
- **H1 hypothesis + final decision:** the real GO bars (≥80% detection and <10% false-positive on *real* model outputs, judge↔human Cohen's κ ≥ 0.60) still require **real LLM-judge calls** and **two human raters** — both owner-gated (spend approval + rater availability). `baseline`, `calibrate`, `tune`, and `scorecard` remain stubs. The H1/H2 GO/NO-GO gate is still open.
- **Pricing:** model capability/pricing profiles remain provisional. The current OpenAI profile marks `gpt-5-mini` active with no sunset and uses $0.60/$2.40/$0.06 per million tokens for input/output/cached input; OpenAI marks it deprecated and lists $0.25/$2.00/$0.025. The profile's cache-write semantics also need validation. The existing hand calculation does not establish cost accuracy with this mismatch, and the zero-priced Ollama profile does not validate a second paid provider's cost accounting.

The existing OpenAI adapter/log is **historical evidence**: OpenAI schedules `gpt-5-mini` for shutdown
on Dec 11, 2026 and recommends `gpt-5.6-terra`. The experiment profile has not been migrated. Any new
H2 live run must use an approved, currently supported model/profile and retain the run report; see
[OpenAI's deprecation notice](https://developers.openai.com/api/docs/deprecations) and
[replacement model](https://developers.openai.com/api/docs/models/gpt-5.6-terra).

**User decisions (2026-10-31):** public release-note/changelog extraction and the proposed JSON schema are
approved; the judge/calibration/sample/repeatability protocol and $10 combined hosted-spend cap are
approved in principle. H2 will use OpenAI `gpt-5.6-terra` plus Ollama. The exact corpus/URLs/hashes,
reviewed Spec Kit feature artifacts, updated H2 profile, and per-call spend preflight remain outstanding.
Two independent human raters are unavailable, so H1 calibration is blocked. Do not run H1 calls or any
paid H2 rerun until the relevant blockers are resolved; see [the MVP spec](../docs/mvp-spec.md#8-judge-calibration-the-make-or-break).

The H2 live run requires `OPENAI_API_KEY` and a reachable Ollama server with the configured model available. Keep inputs synthetic: JSONL logs include prompts and outputs. Do not commit the ignored log or use it for sensitive data.

## Implemented modules

- `ir.py` — `NormalizedRequest`, `NormalizedUsage`, stream events, and `Substitution`.
- `profiles/` — JSON capability/pricing profiles and profile resolution.
- `cost.py` — profile-based cost calculation.
- `providers/` — narrow OpenAI and Ollama streaming adapters plus a small registry.
- `evals/deterministic.py` and `evals/drift.py` — deterministic checks and verdict/quality/efficiency helpers. These are not the H1 experiment: there is no baseline, judge, calibration set, or repeated regression measurement yet.
- `experiments/h2_providers.py` — H2 request, live-call orchestration, console report, and assertions.
- `tests/` — offline unit tests; provider HTTP is mocked, including an independent cost-reconciliation regression check.

## Run

Install the development dependency and run the offline tests from this directory:

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
```

Run the live H2 experiment only when provider spend is approved and Ollama is available:

```bash
export OPENAI_API_KEY=...
python3 -m run h2
```

The CLI appends per-call records to `logs/h2.jsonl`. `h1` runs the offline plumbing harness above (no key, no network); `baseline`, `calibrate`, `tune`, and `scorecard` remain stubs. Preserve the console output and assertion result with any future H2 run; the current JSONL format alone cannot establish the H2 pass bars.
