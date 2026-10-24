"""Per-call JSONL logger (mvp-spec: log every model call).

One JSON object per line; stdlib only. Fields are fixed so the log is trivially loadable for the
scorecard phase. Logs live under mvp/logs/*.jsonl and are gitignored (they contain inputs/outputs).
"""

import json
from pathlib import Path


def log_call(
    path: str | Path,
    *,
    model: str,
    prompt_version: str,
    input,
    output,
    latency_ms: float,
    cost_usd: float,
    success: bool,
) -> None:
    """Append one JSON line describing a single model call."""
    record = {
        "model": model,
        "prompt_version": prompt_version,
        "input": input,
        "output": output,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "success": success,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
