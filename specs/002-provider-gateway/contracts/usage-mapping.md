# Contract — Per-provider usage-field mapping

Authoritative map from each provider's usage object (as surfaced by LiteLLM in streaming mode) → our `NormalizedUsage`. This is the crux of FR-005 and why adapters stay per-provider (research R4). Each row is pinned by a hand-calc cost test (SC-002).

`NormalizedUsage` convention (from `cost.py`): `input_tokens` INCLUSIVE of `cached_input_tokens` + `cache_write_tokens`; `output_tokens` INCLUSIVE of `reasoning_tokens`. `cost.compute_cost` carves each slice out and bills once.

## OpenAI
| NormalizedUsage field | Source |
|---|---|
| `input_tokens` | `usage.prompt_tokens` (already inclusive of cached) |
| `output_tokens` | `usage.completion_tokens` (inclusive of reasoning) |
| `cached_input_tokens` | `usage.prompt_tokens_details.cached_tokens` |
| `cache_write_tokens` | 0 (OpenAI has no separate cache-write count today) |
| `reasoning_tokens` | `usage.completion_tokens_details.reasoning_tokens` |

## Anthropic  ⚠ convention divergence
Anthropic reports `input_tokens` **EXCLUSIVE** of cache tokens (opposite of OpenAI). To honor our inclusive convention we **add them back in**:
| NormalizedUsage field | Source |
|---|---|
| `cached_input_tokens` | `usage.cache_read_input_tokens` |
| `cache_write_tokens` | `usage.cache_creation_input_tokens` |
| `input_tokens` | `usage.input_tokens` **+ cache_read + cache_creation** (make inclusive) |
| `output_tokens` | `usage.output_tokens` |
| `reasoning_tokens` | 0 unless the model/response exposes a reasoning count (extended-thinking) → map if present, else 0 (honest absence) |

> Verify at implementation time exactly how LiteLLM surfaces `cache_creation_input_tokens`/`cache_read_input_tokens` in a streamed Anthropic response; pin with a canned chunk. If LiteLLM pre-normalizes Anthropic into OpenAI's inclusive shape, drop the add-back and pin THAT with a test instead — the test, not the assumption, is authoritative.

## Ollama
| NormalizedUsage field | Source |
|---|---|
| `input_tokens` | `usage.prompt_eval_count` (LiteLLM maps Ollama → `prompt_tokens`; accept either) |
| `output_tokens` | `usage.completion_tokens` / `eval_count` |
| `cached_input_tokens` / `cache_write_tokens` / `reasoning_tokens` | 0 (no breakdown — honest absence, not a guess) |

## Degenerate inputs (FR-005 determinism)
- Missing usage object → `NormalizedUsage(0,0,…)` AND a degraded/`ERROR` signal (a real call with no usage is not a success).
- `reasoning_tokens > output_tokens` or `cached+cache_write > input_tokens` → clamp so `billable_* = max(0, …)` (already how `cost.compute_cost` guards); cost never negative.
