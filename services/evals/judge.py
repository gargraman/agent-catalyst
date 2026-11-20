"""Advisory LLM judge (M3, feature 004) — US3.

Scores candidate quality by calling the SAME `runtime.invoke_result` the playground/published endpoint use
(P6/FR-015) — eval evaluates exactly what production runs. The judge identity is VERSIONED (model + prompt
hash + judge_version) so the judge itself cannot silently drift (P4/FR-017). ADVISORY-ONLY in this feature:
nothing here gates publish (FR-016). Fully mocked in tests (only the transport is patched); no live/paid
call (FR-019). The first live judge call is deferred to Phase 5 under the paid-call protocol.

Honest limitation (position bias): presentation ORDER is randomized (FR-018), but the two responses are
labeled in the prompt. True blind A/B de-labeling is a Phase-5 hardening that only matters for a live judge;
for this mocked, advisory slice the canned judge output is unaffected by it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runtime.invoke import invoke_result
from runtime.providers.profiles import _PROFILE_DIR
from runtime.snapshot import VersionSnapshot

from evals.types import EvalCase, JudgeVerdict

# Pinned, versioned judge identity (clarify 2026-11-05 — resolves the docs-vs-approved Haiku-4.5 skew; 0c).
JUDGE_MODEL = "claude-haiku-5-5"
JUDGE_VERSION = "judge-v1"

_JUDGE_SYSTEM_PROMPT = (
    "You are an impartial evaluation judge. Score the CANDIDATE response on each rubric dimension from 1 "
    "(poor) to 5 (excellent); decide which response better satisfies the task (answer with the true label "
    "'baseline' or 'candidate', or 'tie'); and give a one-sentence rationale. Respond with ONLY strict JSON: "
    '{"rubric": {"<dimension>": <1-5>, ...}, "pairwise": "baseline"|"candidate"|"tie", "rationale": "<text>"}.'
)
# The prompt hash + version together are the judge's content identity (part of the future verdict-cache key).
JUDGE_PROMPT_HASH = hashlib.sha256((_JUDGE_SYSTEM_PROMPT + "|" + JUDGE_VERSION).encode()).hexdigest()[:16]

_PAIRWISE_LABELS = ("baseline", "candidate", "tie")


def _present_order(rng) -> list[str]:
    """Randomize which response is shown first, to reduce position bias (FR-018). Seedable for tests."""
    order = ["baseline", "candidate"]
    rng.shuffle(order)
    return order


def _load_judge_profile_snapshot() -> dict:
    """The frozen capability/pricing profile for the judge model, loaded from the versioned profiles_data."""
    for path in sorted(Path(_PROFILE_DIR).glob("*.json")):
        doc = json.loads(path.read_text())
        if doc.get("provider") == "anthropic" and doc.get("model") == JUDGE_MODEL:
            return doc
    raise FileNotFoundError(f"no capability profile for the judge model {JUDGE_MODEL!r} in {_PROFILE_DIR}")


def _build_judge_snapshot(workspace_id: str) -> VersionSnapshot:
    """A frozen VersionSnapshot for the judge agent — so the judge runs through the identical runtime path."""
    return VersionSnapshot(
        version_id="judge", agent_id="judge", workspace_id=workspace_id, label=JUDGE_VERSION,
        snapshot_hash=f"judge-{JUDGE_PROMPT_HASH}",
        model_config={"provider": "anthropic", "model": JUDGE_MODEL,
                      "params": {"temperature": 0.0, "max_tokens": 1024}},
        profile_snapshot=_load_judge_profile_snapshot(),
        system_prompt=_JUDGE_SYSTEM_PROMPT, prompt_hash=JUDGE_PROMPT_HASH,
        io_contract={"input_schema": {"type": "object", "properties": {"message": {"type": "string"}},
                                      "required": ["message"]},
                     "output_format": "text", "output_schema": None},
        memory_config={}, context_config={}, guardrail_config={},
    )


def _build_judge_prompt(case: EvalCase, baseline_out: str, candidate_out: str, order: list[str]) -> str:
    outs = {"baseline": baseline_out, "candidate": candidate_out}
    dims = ", ".join(d.name for d in case.rubric) or "overall quality"
    blocks = "\n\n".join(f"--- {label} response ---\n{outs[label]}" for label in order)
    # 026 US4: server-authored cases carry structured inputs ({"message": …, vars…}) — render canonically.
    task = json.dumps(case.input, sort_keys=True, ensure_ascii=False) if isinstance(case.input, dict) else case.input
    return (f"Task:\n{task}\n\nRubric dimensions to score (1-5): {dims}\n\n{blocks}\n\n"
            "Return ONLY the strict JSON described in your instructions.")


def _fail_closed(reason: str) -> JudgeVerdict:
    return JudgeVerdict(rubric_scores={}, pairwise_winner="tie", rationale=reason,
                        judge_version=JUDGE_VERSION, judge_model=JUDGE_MODEL,
                        judge_prompt_hash=JUDGE_PROMPT_HASH, low_confidence=True)


async def judge_case(case: EvalCase, baseline_out: str, candidate_out: str, ctx, *, rng=None) -> JudgeVerdict:
    """Judge one case through the shared runtime. Returns a versioned JudgeVerdict; fails closed to
    low_confidence (never crashes, never fabricates scores) on unparseable judge output."""
    import random

    rng = rng if rng is not None else random.Random()
    order = _present_order(rng)
    prompt = _build_judge_prompt(case, baseline_out, candidate_out, order)
    snapshot = _build_judge_snapshot(getattr(ctx, "workspace_id", "") or case.workspace_id)

    result = await invoke_result(snapshot, {"message": prompt}, ctx)  # same runtime path as production (P6)

    try:
        data = json.loads(result.text)
        rubric = {str(k): float(v) for k, v in dict(data["rubric"]).items()}
        pairwise = data["pairwise"]
        if pairwise not in _PAIRWISE_LABELS:
            raise ValueError(f"pairwise must be one of {_PAIRWISE_LABELS}, got {pairwise!r}")
        rationale = str(data.get("rationale", ""))
    except (ValueError, KeyError, TypeError):
        return _fail_closed("judge output was not valid JSON in the expected schema")

    return JudgeVerdict(rubric_scores=rubric, pairwise_winner=pairwise, rationale=rationale,
                        judge_version=JUDGE_VERSION, judge_model=JUDGE_MODEL,
                        judge_prompt_hash=JUDGE_PROMPT_HASH, low_confidence=False)
