"""Layer-1 deterministic checks — mirrors docs/backend/05-eval-drift.md §2 Layer 1 (05:45-61).

Zero model calls. These are the correctness floor and the pre-filter that keeps judge cost down — a case
that fails a HARD assertion never reaches the judge. For this MVP's mock-incident triage task the output
contract is {severity, summary, next_step}.
"""

import json
from dataclasses import dataclass

# severity enum for the triage task
SEVERITY_ENUM = {"critical", "high", "medium", "low", "info"}

# default required fields for the triage output contract (a case may override via case["required_fields"])
DEFAULT_REQUIRED = ("severity", "summary", "next_step")

# refusal phrases → a spiking refusal rate is itself drift (05:60)
REFUSAL_PHRASES = (
    "i cannot", "i can't", "i can not", "i'm sorry", "i am sorry",
    "cannot help", "can't help", "unable to", "i won't", "i will not",
    "as an ai", "i'm not able", "i am not able",
)

_EMPTY_VALUES = (None, "", [], {})


@dataclass
class DetResult:
    # 05:45-49
    passed: bool
    checks: dict[str, bool]   # {"schema_valid": True, "required_fields": False, ...}
    hard_fail: bool           # a hard_fail forces case FAIL regardless of the judge


def _is_empty(output) -> bool:
    if output is None:
        return True
    if isinstance(output, str):
        return not output.strip()
    if isinstance(output, dict):
        return len(output) == 0
    return False


def run_deterministic(output: str | dict, case: dict) -> DetResult:
    """Run the Layer-1 checks for one (output, case).

    Hard-fail conditions (force FAIL, skip the judge): empty output, a refusal phrase, or unparseable/
    schema-invalid JSON. Soft failures (passed=False, hard_fail=False): missing/empty required fields,
    an out-of-enum severity, or an expected-value mismatch.
    """
    checks: dict[str, bool] = {}

    # --- empty-output detection (hard) ---
    if _is_empty(output):
        checks["non_empty"] = False
        return DetResult(passed=False, checks=checks, hard_fail=True)
    checks["non_empty"] = True

    # --- refusal detection (hard; text outputs only) ---
    if isinstance(output, str):
        lowered = output.lower()
        refused = any(phrase in lowered for phrase in REFUSAL_PHRASES)
        checks["no_refusal"] = not refused
        if refused:
            return DetResult(passed=False, checks=checks, hard_fail=True)
    else:
        checks["no_refusal"] = True

    # --- JSON parse / schema validity (hard) ---
    if isinstance(output, str):
        try:
            data = json.loads(output)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON is not an object")
            checks["schema_valid"] = True
        except (ValueError, TypeError):
            checks["schema_valid"] = False
            return DetResult(passed=False, checks=checks, hard_fail=True)
    else:
        data = output
        checks["schema_valid"] = True

    # --- required fields present & non-empty (soft) ---
    required = case.get("required_fields", DEFAULT_REQUIRED)
    checks["required_fields"] = all(
        field in data and data[field] not in _EMPTY_VALUES for field in required
    )

    # --- severity enum (soft) ---
    checks["severity_enum"] = data.get("severity") in SEVERITY_ENUM

    # --- expected-value match when provided (soft) ---
    expected = case.get("expected")
    if expected is not None:
        if isinstance(expected, dict):
            checks["expected_match"] = all(data.get(k) == v for k, v in expected.items())
        else:
            # scalar expected is the expected severity
            checks["expected_match"] = data.get("severity") == expected

    passed = all(checks.values())
    return DetResult(passed=passed, checks=checks, hard_fail=False)
