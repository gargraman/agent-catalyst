// Streaming invoke client (feature 026 US2, T035; research G20 / D16).
//
// POST /api/v1/agents/{id}/invoke answers `text/event-stream`. This module turns that byte stream into typed frames.
// Same portability rule as client.ts: type-only imports of the generated schema, no React / store / Next — the caller
// (store.ts) resolves the dev principal, so auth is still attached in exactly one place (`devAuthHeaders`).
//
// Outcomes:
//  - non-2xx BEFORE the stream (402/429/403/404/503/…) → rejects with the typed `ApiError` (via `parseError`);
//  - a stream that ends WITHOUT a `done` frame → resolves `{ interrupted: true }` (network drop / server crash);
//  - an in-stream `error` frame is delivered to `onFrame` like any other — the server still ends with `done`;
//  - `signal.abort()` → rejects with the fetch AbortError (the caller treats it as a user cancel).
// No 15 s request timeout here: an answer may legitimately stream for longer; the server owns the deadline.
import type { components } from "./schema";
import { API_BASE, devAuthHeaders, parseError, type DevPrincipal } from "./client";

export type InvokeIn = components["schemas"]["InvokeIn"];

// SSE frame payloads — hand-mirrored from services/api/schemas/invoke.py (the frame models aren't referenced by a
// route, so OpenAPI doesn't emit them). Keep in sync with that file.
export type RunStartFrame = { run_id: string; version: string; version_id?: string | null; model: string };
export type UsageFrame = { input_tokens: number; output_tokens: number; cost_usd: number | null; cost_is_estimate: boolean };
export type DoneFrame = { run_id: string; status: string; latency_ms?: number | null; cost_usd?: number | null };
export type ErrorFrame = { code: string; message: string };

/** One citation reference (024): document identity only — never passage text. */
export type Citation = {
  kb_id: string; document_id?: string; document_name?: string; chunk_id?: string; score?: number;
  [k: string]: unknown;
};
export type CitationsFrame = {
  knowledge_used: boolean;
  citations: Citation[];
  knowledge_status?: { kb_id: string | null; status: string; restricted?: boolean }[];
};

/** A decoded SSE frame. Unknown event names pass through as `{event, data}` so a new server frame never breaks the
 *  stream (the UI ignores what it doesn't render). */
export type InvokeFrame =
  | { event: "run_start"; data: RunStartFrame }
  | { event: "text_delta"; data: { text: string } }
  | { event: "reasoning_delta"; data: { text: string } }
  | { event: "usage"; data: UsageFrame }
  | { event: "citations"; data: CitationsFrame }
  | { event: "error"; data: ErrorFrame }
  | { event: "done"; data: DoneFrame }
  | { event: string; data: Record<string, unknown> };

/** Parse as many complete SSE events as `buffer` holds; return them plus the unconsumed tail (a split chunk).
 *  Handles CRLF, multi-line `data:` (joined with "\n" per the SSE spec), `:` comment lines, and events with no
 *  `event:` line (named "message"). A frame whose data isn't JSON is skipped — never thrown — so one bad frame
 *  can't kill a stream. Pure. */
export function parseSseChunk(buffer: string): { frames: InvokeFrame[]; rest: string } {
  // 026 review-2 #10: a CRLF split across two network chunks must not normalise to two LFs (a fake blank line = a
  // fake frame boundary that drops the event's data). Hold a trailing "\r" back until its "\n" (or not) arrives.
  const held = buffer.endsWith("\r") ? "\r" : "";
  const text = (held ? buffer.slice(0, -1) : buffer).replace(/\r\n?/g, "\n");
  const frames: InvokeFrame[] = [];
  let start = 0;
  for (;;) {
    const end = text.indexOf("\n\n", start);
    if (end === -1) break;
    const block = text.slice(start, end);
    start = end + 2;
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;           // blank or comment (keep-alive)
      const i = line.indexOf(":");
      const field = i === -1 ? line : line.slice(0, i);
      let value = i === -1 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length) continue;
    try {
      frames.push({ event, data: JSON.parse(data.join("\n")) } as InvokeFrame);
    } catch {
      /* non-JSON data — skip the frame, keep the stream */
    }
  }
  return { frames, rest: text.slice(start) + held };
}

export type StreamResult = { interrupted: boolean; done?: DoneFrame };

/** Stream one invoke. Resolves when the stream ends; `onFrame` sees every frame in order as it arrives. */
export async function streamInvoke(opts: {
  principal: DevPrincipal;
  agentId: string;
  body: InvokeIn;
  signal?: AbortSignal;
  onFrame: (f: InvokeFrame) => void;
}): Promise<StreamResult> {
  const path = `/api/v1/agents/${encodeURIComponent(opts.agentId)}/invoke`;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: JSON.stringify(opts.body),
    signal: opts.signal,
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...devAuthHeaders(opts.principal) },
  });
  if (!res.ok) throw await parseError(res, path);
  if (!res.body) return { interrupted: true };

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: DoneFrame | undefined;
  const emit = (frames: InvokeFrame[]) => {
    for (const f of frames) {
      if (f.event === "done") done = f.data as DoneFrame;
      opts.onFrame(f);
    }
  };
  try {
    for (;;) {
      const { value, done: eof } = await reader.read();
      if (eof) break;
      const parsed = parseSseChunk(buffer + decoder.decode(value, { stream: true }));
      buffer = parsed.rest;
      emit(parsed.frames);
    }
    emit(parseSseChunk(buffer + decoder.decode() + "\n\n").frames);   // a final event missing its blank line
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") throw e;    // user cancel — caller decides
    return { interrupted: true, done };                              // connection dropped mid-stream
  } finally {
    reader.releaseLock();
  }
  return { interrupted: done === undefined, done };
}
