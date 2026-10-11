/* Plain-language definitions for the ML/LLM vocabulary that shows up in the UI.
   Rendered on hover/focus by <Term id="..."/> (src/components/term.tsx). Keep each
   definition to one or two sentences a non-expert can read. */

export type GlossaryEntry = { term: string; def: string };

export const GLOSSARY: Record<string, GlossaryEntry> = {
  eval: {
    term: "Evaluation (eval)",
    def: "An automated test suite that scores an agent's answers against expected results, so you can tell if a change made it better or worse before shipping.",
  },
  baseline: {
    term: "Baseline",
    def: "A frozen earlier version used as the point of comparison. Each eval measures the current version against the baseline to catch regressions.",
  },
  drift: {
    term: "Drift",
    def: "How much an agent's quality has moved since its baseline, usually after a model, prompt, or knowledge change. Watch or regressed means re-check before relying on it.",
  },
  "eval-pass": {
    term: "Eval pass rate",
    def: "The share of eval test cases the current version answers acceptably. Higher is better; publishing is usually gated on a passing rate.",
  },
  "p95-latency": {
    term: "p95 latency",
    def: "The response time 95 out of 100 requests come in under. A better worst-case measure than the average, which hides slow outliers.",
  },
  temperature: {
    term: "Temperature",
    def: "How much randomness the model uses. Lower (near 0) is focused and repeatable; higher is more varied and creative.",
  },
  "system-prompt": {
    term: "System prompt",
    def: "The standing instructions that define the agent's role, tone, and rules. It applies to every message, unlike what a user types.",
  },
  guardrails: {
    term: "Guardrails",
    def: "Automatic checks on the way in and out, for example redacting PII, blocking off-topic input, or requiring citations, so unsafe or off-policy content is caught.",
  },
  rag: {
    term: "RAG (retrieval-augmented generation)",
    def: "The agent looks up relevant text from your documents first, then answers using that context, so replies are grounded and can cite sources.",
  },
  "embedding-model": {
    term: "Embedding model",
    def: "Turns text into numeric vectors so passages with similar meaning sit close together. It powers semantic search over your knowledge base.",
  },
  chunk: {
    term: "Chunk",
    def: "A document split into smaller passages for retrieval. The agent fetches the most relevant chunks rather than whole files.",
  },
  "top-k": {
    term: "Top-k",
    def: "How many of the most relevant chunks to pull in per question. More gives richer context but costs more tokens and can add noise.",
  },
  "similarity-threshold": {
    term: "Similarity threshold",
    def: "The minimum relevance score a chunk needs to be included. Higher keeps only close matches; lower lets more (and looser) context through.",
  },
  reranker: {
    term: "Reranker",
    def: "A second-pass model that reorders retrieved chunks by true relevance, so the best context lands at the top before the answer is written.",
  },
  "hybrid-search": {
    term: "Hybrid search",
    def: "Combines keyword matching (BM25) with meaning-based vector search, catching both exact terms and paraphrases.",
  },
  mcp: {
    term: "MCP (Model Context Protocol)",
    def: "An open standard for connecting an agent to external tools and data sources. An MCP server exposes a set of tools the agent can discover and call.",
  },
  "sub-agent": {
    term: "Sub-agent",
    def: "A specialized agent another agent can delegate a task to, like calling a helper for a focused job and using its result.",
  },
  token: {
    term: "Tokens",
    def: "The chunks of text models read and write (roughly a few characters each). Usage and cost are measured in tokens.",
  },
  "reasoning-effort": {
    term: "Reasoning effort",
    def: "How much internal deliberation the model spends before answering. Higher can improve hard tasks but adds latency and cost.",
  },
  concurrency: {
    term: "Concurrency",
    def: "How many requests the agent handles at once. A cap protects cost and downstream systems from spikes.",
  },
};
