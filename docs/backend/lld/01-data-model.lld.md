# 01 (LLD) — Data Model & Shared Contracts

> **Parent:** [`../01-data-model.md`](../01-data-model.md) (DDL, enums, RLS, versioning).
> This is the **buildable** layer beneath it: the shared runtime contracts the parent references but never
> defines, every jsonb column as a typed model reconciled to the real frontend, the canonical hash that
> makes immutability enforceable, the lifecycle state machines, an ORM/DAO template, and the
> frontend-contract parity table. Read the parent first for *why*; read this for *the exact types to write*.

## What this adds over parent `01`

| Gap in `01` | Filled here |
|---|---|
| `VersionSnapshot` referenced everywhere (`00 §4`, `03`), `to_snapshot()` is `...` (`01 §5`), **defined nowhere** | §1 — the full frozen dataclass, built from the producer (`01` columns + bindings) **and** cross-checked against every consumer (`02`/`03`/`05`) |
| `RunContext` / `InvokeInputs` only sketched in `00 §4`; missing `session_id` the checkpointer needs | §2 — formal types + the `session_id` amendment |
| 11 jsonb columns typed as bare `dict` (shapes only in DDL comments) | §3 — one Pydantic v2 model per column, each mapped field-by-field to `frontend/src/lib/data.ts` |
| `snapshot_hash`/`content_hash`/`prompt_hash` name sha256 but never fix serialization | §4 — the canonical-JSON algorithm + the exact `snapshot_hash` field set (reconciled with `DRIFT_TRIGGERS`) |
| Lifecycle enums exist; legal transitions are scattered prose | §5 — a transition table per status enum |
| ORM shown for `AgentVersion` only (`01 §5`); other ~30 tables "shape, not every line" | §6 — the relationship/cascade + repository template to clone |
| No single place that proves the model matches the frontend | §7 — the parity table (the "verify models wrt FE" deliverable) with the confirmed mismatches |

All code is Python 3.12, `from __future__ import annotations`, Pydantic **v2** for boundary-validated jsonb
payloads, frozen `@dataclass` for runtime-internal contracts. Snippets are written to compile (see §8).

---

## 1. `VersionSnapshot` — the runtime's single input

`00 §1` states the load-bearing invariant: playground, published invoke, eval, and channel all call
`runtime.invoke(snapshot, inputs, ctx)`, and `invoke()` reads an **immutable `VersionSnapshot`**, never a
live `agents` row. `01 §4.2` freezes that snapshot across `agent_versions` + its binding child rows; `01 §5`
promises `to_snapshot()` materializes it — but leaves the body `...`. This is that type.

**Design rules:**
- Frozen `@dataclass(frozen=True)` — the runtime takes no untrusted input here; it's assembled server-side
  from already-validated rows, so dataclass (not Pydantic) keeps `runtime/` free of validation cost and ORM
  coupling (`01 §5`: "the runtime imports *only* this dataclass, never the ORM").
- Every field is **resolved** (no lazy FK, no DB handle). The embedded jsonb payloads are the §3 Pydantic
  models, already parsed.
- It carries identity (`version_id`, `snapshot_hash`) but **not** mutable columns (`publishable`, `status`)
  — those live on the row, not the frozen snapshot (see §5 for why).

```python
from __future__ import annotations
from dataclasses import dataclass, field

# §3 config models and the §4 hash live in the same package; CapabilityProfile is from 02.
from .configs import (
    ModelConfig, IOContract, MemoryConfig, ContextConfig, GuardrailConfig, RetrievalConfig,
)
from runtime.providers.profiles import CapabilityProfile   # 02 §4


@dataclass(frozen=True)
class RetrievalBindingSnap:
    """One KB a version retrieves from, frozen. Mirrors retrieval_bindings (01 §4.4)."""
    kb_id: str
    kb_index_version: int           # pinned at freeze; stale-warn only — NOT in snapshot_hash (04 §9 / §4 here)
    embedding_model: str            # pinned on the KB (01); a change is a DRIFT_TRIGGER (05)
    retrieval_config: RetrievalConfig
    injection: str                  # 'tool' | 'always_on' (01 §4.4). ⚠️ FE InjectionStrategy is 'tool' | 'stuff'
                                    #   (data.ts) — the API maps FE 'stuff' → 'always_on' on write (the runtime
                                    #   only acts on 'always_on', 04 §6/§7); 'tool' ↔ 'tool'. See §3.1/§7.4.


@dataclass(frozen=True)
class ToolBindingSnap:
    """A tool/MCP tool bound to a version with its schema frozen. Mirrors tool_bindings (01 §4.6)."""
    binding_id: str
    name: str                       # resolved display name (the FE 'http-enrich-ip' / 'mcp:<srv>/<tool>' ref)
    kind: str                       # tool_kind: http|builtin|subagent|mcp
    schema_snapshot: dict           # frozen JSON-schema args (drift source if the server changes)
    config_snapshot: dict
    risk_tier: str                  # read|write|destructive — frozen at bind (01 §4.6)
    requires_approval: bool         # derived at bind: destructive⇒true; write⇒per workspace policy
    subagent_config_id: str | None = None


@dataclass(frozen=True)
class SkillSnap:
    """A bound skill's frozen content. Mirrors skill_bindings + skills (01 §4.4)."""
    skill_id: str
    version: str                    # authored skill version label
    instructions: str              # the instruction fragment injected into the prompt (03 §4)
    resource_uris: tuple[str, ...]  # MinIO refs for bundled reference files (data, not instructions)
    skill_content_hash: str         # pinned; folds into snapshot_hash (01 §4.2, 05 §6)


@dataclass(frozen=True)
class VersionSnapshot:
    # --- identity ---
    version_id: str
    agent_id: str
    workspace_id: str
    label: str                      # "v7" (FE Version.label)
    # --- frozen config (the behavior-defining payload) ---
    system_prompt: str
    model_config: ModelConfig       # provider/model/api_version/params/fallback (02; FE AgentConfig model params)
    profile: CapabilityProfile      # resolved agent_versions.profile_snapshot (02 §4); consumer: 02/03 `snapshot.profile`
    io_contract: IOContract         # typed inputs + output schema (spec B2.5)
    memory_config: MemoryConfig     # session window/compaction/persist (03 §6)
    context_config: ContextConfig   # token-window limit + truncation (03 §7)
    guardrail_config: GuardrailConfig  # ordered stages (06); consumer: 03 input_guardrails/output_guardrails
    semantic_cache_enabled: bool = False   # per-agent opt-in for the semantic response cache (07 §4.3); is_qa_safe() still fences it (2026-10-24 review)
    retrieval_bindings: tuple[RetrievalBindingSnap, ...] = ()
    tool_bindings: tuple[ToolBindingSnap, ...] = ()
    skills: tuple[SkillSnap, ...] = ()     # consumer: 03 build_messages → compose_skill_fragments
    # --- hashes (identity of the frozen content; §4) ---
    prompt_hash: str = ""           # sha256(system_prompt)
    snapshot_hash: str = ""         # sha256 over all behavior-defining fields (§4) → version identity (05)
```

### 1.1 Both-ends completeness check (why these fields and no others)

The anti-rework tactic from the plan: a field exists only if **both** the producer and a consumer agree.

| Snapshot field | Producer (`01`) | Consumer (verified) |
|---|---|---|
| `system_prompt`, `prompt_hash` | `agent_versions.system_prompt` / `.prompt_hash` | `03` `build_messages` → `render_prompt(snapshot.system_prompt)` |
| `model_config` | `agent_versions.model_config` | `02` `build_normalized_request` (provider/model/api_version/params/fallback) |
| `profile` | `agent_versions.profile_snapshot` | `03` `gateway.stream(..., profile=snapshot.profile)`; `02` cost/limits |
| `io_contract` | `agent_versions.io_contract` | `03`/`08` input validation; `02` `response_format` |
| `memory_config` | `agent_versions.memory_config` | `03 §6` `apply_memory_window(..., snapshot.memory_config)` |
| `context_config` | `agent_versions.context_config` | `03 §7` context window enforcement |
| `guardrail_config` | `agent_versions.guardrail_config` | `03` `run_input_stages(..., snapshot.guardrail_config)` |
| `retrieval_bindings` | `retrieval_bindings` rows | `03`/`04` `retriever.search(snapshot, ...)` |
| `tool_bindings` | `tool_bindings` rows | `03` `build_normalized_request` (tools), `tool_approval_gate` (`binding.risk_tier`/`requires_approval`) |
| `skills` | `skill_bindings` + `skills` | `03 §4` `compose_skill_fragments(snapshot.skills)` |
| `snapshot_hash` | `agent_versions.snapshot_hash` | `05` verdict cache key (`candidate_hash = snapshot_hash`) |

> ⚠️ **Amendment to `04 §6`:** the retriever reads `snapshot.retrieval_binding` (**singular**), but `01 §4.4`
> models `retrieval_bindings` as a per-version collection (`unique (version_id, kb_id)` ⇒ a version may bind
> **multiple** KBs). The snapshot is therefore `retrieval_bindings: tuple[...]` (plural); `04`'s `search()`
> should iterate bindings (or select one by the model's tool call). Flagged for the `04` LLD, not edited here.

### 1.2 `to_snapshot()` — the assembly (fills `01 §5`)

```python
def to_snapshot(av: AgentVersion, *, profile: CapabilityProfile) -> VersionSnapshot:
    """Pure read; assembles the frozen snapshot from a loaded AgentVersion + its eager-loaded bindings.
    No live `agents` row is touched (00 §1). `profile` is the resolved agent_versions.profile_snapshot."""
    return VersionSnapshot(
        version_id=str(av.id), agent_id=str(av.agent_id), workspace_id=str(av.workspace_id), label=av.label,
        system_prompt=av.system_prompt,
        model_config=ModelConfig.model_validate(av.model_config_),      # jsonb → §3 model
        profile=profile,
        io_contract=IOContract.model_validate(av.io_contract),
        memory_config=MemoryConfig.model_validate(av.memory_config),
        context_config=ContextConfig.model_validate(av.context_config),
        guardrail_config=GuardrailConfig.model_validate(av.guardrail_config),
        retrieval_bindings=tuple(
            RetrievalBindingSnap(
                kb_id=str(b.kb_id), kb_index_version=b.kb_index_version, embedding_model=b.kb.embedding_model,
                retrieval_config=RetrievalConfig.model_validate(b.retrieval_config), injection=b.injection,
            ) for b in av.retrieval_bindings
        ),
        tool_bindings=tuple(
            ToolBindingSnap(
                binding_id=str(b.id), name=_binding_name(b), kind=b.kind, schema_snapshot=b.schema_snapshot,
                config_snapshot=b.config_snapshot, risk_tier=b.risk_tier,
                requires_approval=b.requires_approval, subagent_config_id=_opt_str(b.subagent_config_id),
            ) for b in av.tool_bindings
        ),
        skills=tuple(
            SkillSnap(
                skill_id=str(b.skill_id), version=b.skill.version, instructions=b.skill.instructions,
                resource_uris=tuple(r.uri for r in b.skill.resources), skill_content_hash=b.skill_content_hash,
            ) for b in av.skill_bindings
        ),
        prompt_hash=av.prompt_hash, snapshot_hash=av.snapshot_hash,
    )
```

---

## 2. `RunContext` and `InvokeInputs`

`00 §4` gives `RunContext` as a prose dataclass and `03 §3` references it as `ctx` plus adds fields
(`depth`, `parent_run_id`, `subagent_budget`, `approval_expiry_s`, `ctx.child()`). This consolidates them
into one formal type and fixes the `session_id` gap.

```python
from __future__ import annotations
from dataclasses import dataclass, field, replace
import time

from runtime.budget import BudgetGuard          # 07 §3
from access_policy import Principal              # {role, teams, subject?} — access-policy.ts mirror (09)


@dataclass(frozen=True)
class RunContext:
    workspace_id: str
    run_source: str                 # run_source enum: playground|api|eval|channel|tool_test|subagent (01 §3)
    trace_id: str
    budget: BudgetGuard             # per-run + per-tenant spend guard (07)
    principal_id: str | None = None     # console user (playground); None for token/channel callers
    caller_token_id: str | None = None  # published-token caller; None for console
    principal: Principal | None = None  # RESOLVED identity (role + teams + subject?) for per-caller access checks
                                        #   (03 tool gate, 04 retrieval ACL, invoke access-policy). Resolved once at
                                        #   invoke from the session/token/channel identity. 2026-10-24 review (04 needs it).
    idempotency_key: str | None = None
    session_id: str | None = None   # ⚠️ see amendment below — needed for the checkpointer thread_id (03 §6)
    # feature switches the callers set differently:
    allow_cache: bool = True        # eval sets False (05 §7)
    stream: bool = True
    deadline_s: float = 30.0        # hard wall-clock budget (p99 SLO, 10 §5)
    approval_expiry_s: float = 3600.0   # how long a tool approval may wait; does NOT consume deadline_s (03 §4)
    # sub-agent delegation (P2):
    depth: int = 0
    parent_run_id: str | None = None
    subagent_budget: "BudgetGuard | None" = None
    _started_monotonic: float = field(default_factory=time.monotonic)  # stamped when invoke() builds the ctx

    def past_deadline(self) -> bool:
        return (time.monotonic() - self._started_monotonic) >= self.deadline_s

    def tool_time_left(self) -> float:
        """Remaining wall-clock the agentic tool loop may still spend (03 §8)."""
        return max(0.0, self.deadline_s - (time.monotonic() - self._started_monotonic))

    def child(self) -> "RunContext":
        """A sub-agent context: depth+1, this run as parent, decremented sub-agent budget (03 §8).
        Inherits workspace_id so a sub-agent can never cross tenant (09 §8)."""
        return replace(
            self, depth=self.depth + 1, parent_run_id=self.trace_id,
            subagent_budget=_decrement(self.subagent_budget),
        )
```

```python
@dataclass(frozen=True)
class Message:
    role: str                       # 'user' | 'assistant' | 'system' | 'tool'
    content: str


@dataclass(frozen=True)
class InvokeInputs:
    messages: list[Message]
    inputs: dict                    # declared typed vars, validated vs snapshot.io_contract (08, spec B2.5)
```

> ⚠️ **Amendment to `00 §4` + `03 §3`.** `03 §6` keys the checkpointer `thread_id = f"{ws}:{agent}:{session}"`
> and `GraphState` reads tool state, yet **neither `RunContext` (`00 §4`) nor `GraphState` (`03 §3`) carries
> `session_id`**, and `GraphState` is missing `pending_tool_calls` (read by `tool_approval_gate`/`execute_tools`
> in `03 §4`). This LLD adds `session_id` to `RunContext` and records both as corrections the `03` LLD must
> propagate into `GraphState` (`pending_tool_calls: list[...]`, and `session_id` sourced from `ctx`). Not
> edited in the parent per the reference-don't-restate rule.

---

## 3. jsonb columns as typed models

Each `agent_versions` / binding jsonb column (`01 §4.2`, `§4.4`, `§4.6`) is a Pydantic v2 model. These are
validated when a draft config is saved (`08` `PATCH /agents/:id`) and when `to_snapshot()` parses a row.
The FE source of truth is `AgentConfig` in `data.ts` — **note it is flat and the backend splits it across
five columns**; the mapping below is the reconciliation.

### 3.1 The FE-flat → backend-split map (verified)

| FE `AgentConfig` field | Backend column → model | Field in model |
|---|---|---|
| `temperature`, `maxOutputTokens`, `reasoningEffort`, `fallbackModel` + `Agent.provider`/`model` | `model_config` → `ModelConfig` | `params.temperature`, `params.max_tokens`, `params.reasoning_effort`, `fallback`, `provider`, `model` |
| `outputFormat`, `includeCitations`, `inputVariables` | `io_contract` → `IOContract` | `output_format`, `include_citations`, `input_variables` |
| `sessionMemory`, `maxTurnsRetained`, `longTermMemory` | `memory_config` → `MemoryConfig` | `persist`, `window_turns`, `long_term` |
| `truncationStrategy` | `context_config` → `ContextConfig` | `truncation_strategy` |
| `retrieval.{topK,similarityThreshold,reranker,maxInjectedTokens,hybrid}` | per-KB `retrieval_bindings[].retrieval_config` → `RetrievalConfig` | `top_k`, `similarity_threshold`, `reranker`, `max_ctx_tokens`, `hybrid` |
| `retrieval.injection` (`tool`\|`stuff`) | per-KB `retrieval_bindings.injection` **column** (not `RetrievalConfig`) | `tool`→`tool`, `stuff`→`always_on` (API maps on write; runtime acts on `always_on`, 04 §6/§7) |
| `guardrails.{input,output}[]` (`{name,desc,on}`) | `guardrail_config` → `GuardrailConfig` | `input[]`, `output[]` of `StageConfig` |
| `spend.{monthlyCapUsd,maxConcurrency,timeoutSec}` | `spend_limits` + `concurrency_limits` + `workspaces.request_timeout_s` (**not** a version jsonb) | — |

> **Naming:** FE is `camelCase`, backend is `snake_case` (`00 §8`). Pydantic models use `snake_case` fields
> with `alias`/`populate_by_name` so a `camelCase` payload from the console validates directly.

### 3.2 The models

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class _Cfg(BaseModel):
    """Base: accept camelCase (FE) or snake_case, forbid unknown keys (catch typos at the boundary)."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ParamsConfig(_Cfg):
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(4096, alias="maxOutputTokens", ge=1)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = Field("high", alias="reasoningEffort")


class FallbackConfig(_Cfg):
    provider: str
    model: str


class ModelConfig(_Cfg):
    provider: str                   # provider_name enum value (01 §3), lowercase
    model: str
    api_version: str | None = Field(None, alias="apiVersion")
    params: ParamsConfig = ParamsConfig()
    fallback: FallbackConfig | None = None   # FE fallbackModel resolves to {provider, model}


class InputVariable(_Cfg):
    name: str
    type: Literal["string", "number", "boolean", "enum"]
    required: bool = True
    default: str | None = None


class IOContract(_Cfg):
    input_variables: list[InputVariable] = Field(default_factory=list, alias="inputVariables")
    output_format: Literal["text", "json"] = Field("json", alias="outputFormat")
    include_citations: bool = Field(True, alias="includeCitations")
    output_schema: dict | None = None        # JSON schema when output_format == "json" (drives det. schema check, 05)


class MemoryConfig(_Cfg):
    persist: bool = Field(True, alias="sessionMemory")       # session memory on/off
    window_turns: int = Field(24, alias="maxTurnsRetained", ge=0)
    compaction: Literal["summarize", "drop_oldest"] = "summarize"
    long_term: bool = Field(False, alias="longTermMemory")   # P2


class ContextConfig(_Cfg):
    # window_limit None ⇒ use the model profile's limits.context_tokens (03 §7)
    window_limit: int | None = None
    truncation_strategy: Literal["summarize", "drop_oldest"] = Field("summarize", alias="truncationStrategy")


class StageConfig(_Cfg):
    """One guardrail stage as stored on a version. FE GuardrailStage is {name, desc, on};
    the backend keys the catalog stage by `stage` id and carries per-stage `params` (06)."""
    stage: str = Field(alias="name")        # catalog id: pii|secret_leak|prompt_injection|topic_scope|...
    enabled: bool = Field(True, alias="on")
    params: dict = Field(default_factory=dict)   # per-stage thresholds/patterns (typed per stage in the 06 LLD)


class GuardrailConfig(_Cfg):
    input: list[StageConfig] = Field(default_factory=list)
    output: list[StageConfig] = Field(default_factory=list)
    fail_mode: Literal["closed", "open"] | None = None   # per-version override of AC_GUARDRAILS_FAIL_MODE (06 §3/§6); None → global default (2026-10-24 review)


class RetrievalConfig(_Cfg):
    # NOTE: `injection` is NOT here — it is a per-binding column (RetrievalBindingSnap.injection / 01 §4.4),
    #   not a retrieval-tuning field. Modeling it in both places (earlier draft) let the two disagree. Single
    #   source of truth = the binding. FE `AgentConfig.retrieval.injection` maps to that column (§3.1).
    top_k: int = Field(6, alias="topK", ge=1)
    similarity_threshold: float = Field(0.2, alias="similarityThreshold", ge=0.0, le=1.0)
    reranker: str | None = None              # FE "bge"|"none" → None when "none"
    max_ctx_tokens: int = Field(2000, alias="maxInjectedTokens", ge=0)
    hybrid: bool = False
    metadata_filters: dict = Field(default_factory=dict)


class ChunkConfig(_Cfg):
    size: int = Field(800, ge=1)             # tokens (04 §4)
    overlap: int = Field(120, ge=0)
    structure_aware: bool = True
    # ⚠️ FE folds KnowledgeBase.chunkConfig.indexType ("HNSW"|"IVF") in here; backend models the index at the
    # DDL level (01 §4.4), not in chunk_config. Parity note in §7 — index_type is NOT a chunk_config field.


class EgressPolicy(_Cfg):
    """config.egress on tools / mcp_servers (01 §4.6)."""
    mode: Literal["deny_by_default"] = "deny_by_default"
    allow: "EgressAllow" = Field(default_factory=lambda: EgressAllow())


class EgressAllow(_Cfg):
    domains: list[str] = Field(default_factory=list)
    cidrs: list[str] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=list)


class AccessPolicy(_Cfg):
    """agents/kb/tools/mcp/channels/skills .access_policy (01 §4.2). ONE shape shared FE↔backend
    (frontend access-policy.ts). Roles are LOWERCASE and MAY include 'admin' (see §7 role-casing note)."""
    visibility: Literal["workspace", "restricted", "private"] = "workspace"
    use_roles: list[str] = Field(default_factory=list, alias="useRoles")
    use_teams: list[str] = Field(default_factory=list, alias="useTeams")
    manage_roles: list[str] = Field(default_factory=list, alias="manageRoles")
    manage_teams: list[str] = Field(default_factory=list, alias="manageTeams")
    owner: str | None = None        # set only when visibility == "private"
```

`EgressPolicy.model_rebuild()` after `EgressAllow` is defined (forward ref). The 06 LLD will specialize
`StageConfig.params` into a typed union per stage id; here it stays `dict` so every column has a validator
today without pulling the whole guardrail catalog into the data-model layer.

---

## 4. Canonical hash serialization

`01 §4.2` and `05 §6` make correctness depend on three sha256 hashes but never fix **how bytes are produced**
— and an unstable hash is a silent correctness failure: if `snapshot_hash` changes for an unrelated reason,
the eval verdict cache (`05 §6`) misses and re-bills; if it *fails to change* on a behavior edit, the publish
gate (`05 §5`) is bypassed and a regressed agent ships. So the serialization is a hard contract.

### 4.1 `canonical_json` — the one serializer

```python
from __future__ import annotations
import hashlib, json
from decimal import Decimal


def _normalize(v):
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")      # Decimal → canonical string (never float — binary drift)
    if isinstance(v, float):
        return format(Decimal(str(v)).normalize(), "f")    # float → canonical decimal string (no binary drift)
    if isinstance(v, dict):
        return {k: _normalize(v[k]) for k in sorted(v)}   # keys sorted
    if isinstance(v, (list, tuple)):
        return [_normalize(x) for x in v]                  # order preserved for SEMANTIC lists (messages);
                                                           #   binding lists are pre-sorted by a STABLE key (§4.2)
    return v


def canonical_json(obj) -> bytes:
    return json.dumps(
        _normalize(obj), separators=(",", ":"), ensure_ascii=False, sort_keys=False,
    ).encode("utf-8")                            # sorting already done in _normalize; keep insertion order of lists


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()
```

Rules that make it stable across processes/machines: **keys sorted**, **list order preserved** (for semantic
lists; binding lists are pre-sorted, §4.2), **no whitespace**, **UTF-8**, **floats normalized to a canonical
decimal string** (so `temperature: 0.2` and `similarity_threshold: 0.2` hash identically everywhere — the raw
jsonb carries floats, so `_normalize` *converts* them rather than rejecting; money/scores are already
`Decimal`), unknown-key-free inputs (the §3 models use `extra="forbid"`).

### 4.2 The three hashes

```python
def compute_prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def compute_skill_content_hash(instructions: str, resource_hashes: list[str], version: str) -> str:
    # 01 §4.4: sha256 over {instructions + ordered resource content_hashes + version}
    return sha256_hex({"instructions": instructions, "resources": resource_hashes, "version": version})


def compute_snapshot_hash(av: AgentVersion) -> str:
    """The version's content identity. Covers EVERY DRIFT_TRIGGER (05 §5) — plus io_contract as deliberate
    safe over-coverage (see §4.3). A CI property test (05 §5 'C2') mutates each trigger and asserts this hash
    changes (the forward direction); io_contract is the one hashed field that is not itself a trigger."""
    payload = {
        "model": av.model_config_["model"],
        "provider": av.model_config_["provider"],
        "api_version": av.model_config_.get("api_version"),
        "params": av.model_config_.get("params", {}),
        "prompt_hash": av.prompt_hash,
        "guardrail_config": av.guardrail_config,
        "io_contract": av.io_contract,
        "tool_bindings": [
            {"kind": b.kind, "schema": b.schema_snapshot, "risk_tier": b.risk_tier,
             "requires_approval": b.requires_approval, "subagent_config_id": _opt_str(b.subagent_config_id)}
            # sort by STABLE identity (the bound tool), NOT the per-bind row UUID — else a clone of an identical
            # version hashes differently and needlessly busts the verdict cache.
            for b in sorted(av.tool_bindings, key=lambda b: str(b.tool_id or b.mcp_tool_id))
        ],
        "skill_bindings": [
            {"skill_id": str(b.skill_id), "content_hash": b.skill_content_hash}
            for b in sorted(av.skill_bindings, key=lambda b: str(b.skill_id))   # skill_id stable across clones
        ],
        "retrieval_bindings": [
            {"kb_id": str(b.kb_id), "embedding_model": b.kb.embedding_model,
             "config": b.retrieval_config, "injection": b.injection}
            # injection IS part of the retrieval_config DRIFT_TRIGGER (05 §5) but lives in its own column (01 §4.4),
            # so it must be hashed explicitly — else toggling tool↔always_on bypasses the publish gate (review H2).
            # kb_index_version deliberately EXCLUDED — KB content is live & independent (04 §9, parked C3).
            for b in sorted(av.retrieval_bindings, key=lambda b: str(b.kb_id))   # kb_id stable across clones
        ],
        "profile_features": av.profile_snapshot.get("features", {}),   # capability_substitution driver (02 §5)
    }
    return sha256_hex(payload)
```

### 4.3 `snapshot_hash` field set vs `DRIFT_TRIGGERS` (the reconciliation)

| `DRIFT_TRIGGERS` (05 §5) | Covered by `compute_snapshot_hash` via |
|---|---|
| `model`, `provider`, `api_version` | `model`, `provider`, `api_version` |
| `params` | `params` |
| `prompt_hash` | `prompt_hash` |
| `guardrail_config` | `guardrail_config` |
| `tool_bindings` | `tool_bindings[]` (+ frozen `risk_tier`/`requires_approval`/`subagent_config_id`, 01 §4.6) |
| `skill_bindings`, `skill_content_hash` | `skill_bindings[].content_hash` |
| `embedding_model`, `kb_binding` | `retrieval_bindings[].embedding_model` / `.kb_id` |
| `retrieval_config` (incl. `injection`, per 05 §5) | `retrieval_bindings[].config` **+ `.injection`** (separate column, hashed explicitly — review H2) |
| `capability_substitution` | `profile_features` (a profile feature change flips a substitution) |
| — (`kb_index_version`) | **deliberately excluded** — KB content live & independent (04 §9; parked, 05 §6) |
| — (`io_contract`) | **hashed anyway** — NOT a `DRIFT_TRIGGER`, but a changed IO contract changes behavior; safe over-coverage (busts the cache, never a false PASS). The C2 reverse-direction test must whitelist it. |

This is the asymmetry `01 §4.2` calls out: **skill content is pinned** into the hash (instructions = pinned,
like the prompt); **KB content is not** (reference data = live). The table is the C2 property-test spec.

---

## 5. Lifecycle state machines

The enums are in `01 §3`; the legal transitions are scattered across `01`/`05`/`08`. Here they are as
tables. **Flags beside the machine, not states:** `agents.registered_at` (14), `agent_versions.publishable`
(05), and the FE `needsReEval` proxy are booleans that gate actions — they are *not* status values and do not
appear below (this is why `VersionSnapshot` omits `publishable`, §1).

### 5.1 `agent_status` (`draft | published | archived | eval_failing`)

| From → To | Trigger | Actor / claim | Notes |
|---|---|---|---|
| `draft → eval_failing` | eval run returns `fail` (pre-publish) | eval worker | FE `addEvalRun` transition |
| `eval_failing → draft` | eval run returns `pass` | eval worker | clears the failing state |
| `draft → published` | `publishAgent` passes the gate (`05 §5`) | `agent.publish` (publisher+) | blocked if `eval_failing`/`needsReEval`/low-confidence |
| `published → draft` | `unpublishAgent` | `agent.publish` | endpoint stops serving |
| `* → archived` | `archiveAgent` | `agent.delete` (admin) | reversible via `restoreAgent → draft` |
| `archived → draft` | `restoreAgent` | `agent.delete` | |

`eval_failing` only ever applies pre-publish; a published agent that later fails a *re-eval* surfaces via
`drift` + `needsReEval`, not by moving to `eval_failing` (that would take the endpoint down silently).

### 5.2 `version_status` (`draft | evaluated | published | deprecated`)

| From → To | Trigger | Notes |
|---|---|---|
| `draft → evaluated` | an eval run completes against this version (any verdict) | `publishable` set only on PASS |
| `evaluated → published` | this version becomes an agent's `active_version` via publish/re-point | gate enforced (`05 §5`) |
| `published → deprecated` | a different version is published/re-pointed over it | old endpoints already pin by id |
| `draft → draft` | editing a frozen field | **new row** (immutability, `01 §4.2`), never an in-place update |

### 5.3 `approval_status` (`pending | approved | denied | expired`) — P2

| From → To | Trigger | Actor |
|---|---|---|
| `pending → approved` | `decideApproval("approve")` → `Command(resume="approve")` (`03 §4`) | `tool.approve` (publisher+) |
| `pending → denied` | `decideApproval("deny")` | `tool.approve` |
| `pending → expired` | `now() > expires_at` (`approval_expiry_s`) | system; resumed branch takes the denied path |

Terminal states are immutable. Expiry is wall-clock vs `tool_approvals.expires_at`, independent of
`ctx.deadline_s` (a parked run doesn't tick the latency deadline, `03 §4`).

### 5.4 `job_status` (`queued | running | completed | failed | cancelled`)

`queued → running → {completed | failed}`; `queued|running → cancelled` on client/operator cancel. No
transition out of a terminal state (a retry is a **new** `jobs` row).

### 5.5 `run_status` (`success | error | blocked`)

Set **once** at run end — not a progression. `blocked` = a guardrail BLOCK (`06`); `error` = unrecoverable
(provider exhausted after fallback, `02 §6`); `success` = completed (including a clean `deadline_exceeded`
partial, `03 §5`).

---

## 6. ORM & DAO conventions

`01 §5` shows `AgentVersion` and the `Base`/`WorkspaceScoped` mixins, then says "shape, not every line." This
is the template to clone for the other ~30 tables so they're consistent.

```python
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Base, WorkspaceScoped: see 01 §5.

class Agent(Base, WorkspaceScoped):
    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str]
    name: Mapped[str]
    status: Mapped[str]                      # agent_status enum (string at the ORM; native enum in DDL)
    owner_subject: Mapped[str]
    access_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"visibility": "workspace"})
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=True,   # circular FK (01 §4.2)
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # relationships: a version is owned by its agent; deleting the agent cascades its versions.
    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", foreign_keys="AgentVersion.agent_id",
    )
```

**Conventions (apply to every model):**
- **`WorkspaceScoped` mixin on every tenant table** → inherits `workspace_id` + the RLS policy loop (`01 §6`).
  The §7 CI check asserts every `WorkspaceScoped` table has an RLS policy.
- **Cascade = ownership.** A child that cannot outlive its parent (`agent_versions`, `retrieval_bindings`,
  `eval_results`, `skill_resources`) uses `cascade="all, delete-orphan"`. A reference that should block
  deletion (a `published_endpoints.pinned_version_id`) uses a plain FK + `RESTRICT` — never silently cascade a
  live endpoint away.
- **Loading strategy:** `to_snapshot()` needs bindings eagerly → load `AgentVersion` with
  `selectinload(AgentVersion.retrieval_bindings, .tool_bindings, .skill_bindings)`. Everything else is lazy.
- **Repository pattern** (keeps `routers/` thin and RLS-safe — the session already has `SET LOCAL
  app.workspace_id`, so repos never add a manual `WHERE workspace_id`):

```python
class AgentRepo:
    def __init__(self, db): self.db = db
    def get(self, agent_id: str) -> Agent | None:
        return self.db.get(Agent, agent_id)                 # RLS filters tenant; None if cross-tenant
    def list(self, *, limit: int, cursor: str | None) -> tuple[list[Agent], str | None]:
        ...                                                 # cursor pagination convention → 00-conventions.lld.md (subtask 2)
    def create(self, **cols) -> Agent: ...
```

---

## 7. Frontend contract parity (the "verify models wrt frontend" deliverable)

Verified against `frontend/src/lib/{data,access-policy,permissions,iam,tools,mcp}.ts` (read directly, not
from memory). ✅ = matches, ⚠️ = a real mismatch the backend must handle deliberately.

### 7.1 Enums / unions

| Concept | Frontend | Backend (`01 §3`) | Verdict |
|---|---|---|---|
| Agent status | `AgentStatus = published\|draft\|archived\|eval_failing` | `agent_status` same 4 | ✅ |
| Version status | `Version.status = published\|evaluated\|draft\|deprecated` | `version_status` same 4 | ✅ |
| Verdict | `Verdict = pass\|fail\|low_confidence`; `Version.evalVerdict` adds `"none"` | `eval_verdict` = 3 (no `none`) | ⚠️ `"none"` is a **FE display-only** sentinel for "never evaluated" (DB models it as *no eval_run row*). API maps absent → `"none"`. |
| Drift | `Agent.drift = stable\|watch\|regressed\|none` | `drift_state` = `none\|stable\|watch\|regressed` | ✅ (same set) |
| Provider | `ModelRef.provider = Anthropic\|OpenAI\|Google\|Mistral\|Meta` (**Title-case**) | `provider_name = anthropic\|openai\|google\|mistral\|meta\|azure\|bedrock` (**lowercase**, +2) | ⚠️ case + 2 extra. API lowercases on write, Title-cases known values on read; `azure`/`bedrock` have no FE enum entry yet. |
| Run source | `Run.source = Playground\|API\|Eval\|Channel\|Tool test\|Subagent` (**Title-case, spaces**) | `run_source = playground\|api\|eval\|channel\|tool_test\|subagent` | ⚠️ pure display mapping; `tool_test`↔"Tool test", `api`↔"API". |
| KB status | `ready\|indexing\|error` | `data_sources.status` text `synced\|syncing\|error`; KB status derived | ⚠️ KB-level status is a **rollup** the API computes; not a stored column. |
| Reasoning effort | `low\|medium\|high\|xhigh\|max` | `ParamsConfig.reasoning_effort` same 5 (§3) | ✅ |
| Assertion kind | `schema_valid\|required_fields\|regex\|exact\|tool_called\|no_refusal\|has_citation` | stored in `eval_cases.assertions` jsonb (05 §2a) | ✅ (7 values; typed in the 05 LLD) |

### 7.2 Roles — the easy-to-get-wrong one

Three different role vocabularies coexist; the backend must not conflate them:

| Where | Values | Casing |
|---|---|---|
| `permissions.ts Role` (RBAC + `access_policy`) | `viewer, builder, publisher, admin` | lowercase, **4** |
| `access-policy.ts CallerRole` | `Role \| "executer"` | lowercase, **5** |
| `workspace_role` enum (`01 §3`, per-workspace membership) | `viewer, builder, publisher` | lowercase, **3** (no `admin`, no `executer`) |
| `Member.role` (read-only roster display) | `Admin, Publisher, Builder, Viewer` | **Title-case**, 4 |

**The rule to encode:** `access_policy.use_roles/manage_roles` are the **4-value lowercase `Role`** and *may
contain `admin`* (and `executer` for caller gating), even though `workspace_role` the enum **cannot** — because
`admin` is an app-wide IdP claim (`ap-platform-admin`, `01 §4.9`) and `executer` rides on tokens/channels, not
workspace membership. `Member.role` is a Title-case **projection for display only** (`01 §4.1`: the Members tab
is a read-only claim projection, no SoR row). Mixing these up (e.g. validating `access_policy.use_roles`
against the `workspace_role` enum) would reject every `admin`/`executer` policy — a real bug this table exists
to prevent. `AccessPolicy` (§3.2) therefore types roles as `list[str]`, not the 3-value enum.

### 7.3 `access_policy` shape

FE `AccessPolicy` (`access-policy.ts`) = `{visibility: workspace|restricted|private, useRoles[], useTeams[],
manageRoles[], manageTeams[], owner?}` — **matches** the `01 §4.2` DDL comment and the §3.2 `AccessPolicy`
model exactly. ✅ No change. (Caller gating nuance — an `executer` is gated on **teams only**, a role-only
`restricted` policy denies callers fail-closed — is an invoke-time rule for the `09` LLD, not a shape change.)

### 7.4 Structural gotchas the backend owns

| FE shape | Backend reality |
|---|---|
| `Agent.tools: string[]` mixes `"http-enrich-ip"` and `"mcp:<serverId>/<tool>"` (`tools.ts MCP_REF_PREFIX`) | `tool_bindings` rows (`kind` + `tool_id`\|`mcp_tool_id`); the API resolves the ref ↔ binding. `ToolBindingSnap.name` carries the ref. |
| `Agent.needsReEval?: boolean` | **inverse of** `agent_versions.publishable` (`01 §4.2`): a trigger edit sets `needsReEval=true`/`publishable=false`; a fresh PASS clears it. |
| `Agent.endpoint?` (URL slug) ≠ `Agent.id` | `published_endpoints.slug` vs `agents.slug`; endpoint assigned on publish (`endpoint ?? id`). |
| `ApiToken.agent`, `IngestionJob.kb` link **by display name** | backend keys by id; the name is denormalized for display + the FE cascade-delete. API must resolve name→id. |
| `RepoTool` identity = **`name`** (no id) | `tools.name` unique per workspace; `tools.id` exists but the FE addresses tools by name. |
| Credentials everywhere = `{masked, vaultPath}` (`CredentialRef`) | raw secret write-only to Vault; only `masked` + `vault_path` persisted (`01 §4.3`, `§4.4`, `§4.6`). Never return the secret. |
| `KnowledgeBase.chunkConfig.indexType: HNSW\|IVF` | backend `chunk_config` has **no** `index_type` (§3.2); the index is a DDL/`04` concern. API accepts+ignores it or surfaces it from KB metadata. |
| `McpServer.auth` seen as `oauth\|bearer\|none` | `mcp.ts McpAuth = none\|bearer\|oauth\|headers` (**4th: `headers`**); `mcp_servers.config` vaults the credential. |
| Workspace FE `{id,name,short,groupPrefix}` | `workspaces` has `slug`,`name`,`idp_binding` (jsonb); `groupPrefix` is the **Okta-adapter sugar** for `idp_binding` (`iam.ts idpBinding`, `18 §3`); `short` is FE-derived (`shortOf`). |

---

## 8. Verification performed

Re-run these on any edit to this doc (the plan's per-subtask protocol):

1. **Cross-ref integrity** — every `01/02/03/04/05/06 §x` link points at a section that exists in the parent.
2. **FE parity** — every union/field in §7 was grepped from the live `frontend/src/lib/*.ts` (not memory);
   the ⚠️ rows are the actual deltas found.
3. **Both-ends completeness** — §1.1 cross-checks every `VersionSnapshot` field against both `01` columns and
   a named `02`/`03`/`05` consumer; no orphan fields.
4. **Snippet compile** — §1–§4 Python extracted and run through `python3 -m py_compile` (with light import
   stubs for cross-module names like `BudgetGuard`, `CapabilityProfile`, `AgentVersion`).
5. **Internal consistency** — the three ⚠️ amendments (`session_id`/`pending_tool_calls` in `03`; singular
   `retrieval_binding` in `04`) are flagged as callouts, not silent parent edits.

→ Next LLD: `00-conventions.lld.md` (exception hierarchy, pagination, idempotency — subtask 2).
