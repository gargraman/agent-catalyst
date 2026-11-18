// Code review 2026-11-18 #5 — the eval-case adapter round-trip must be lossless. Opening a server-authored case and
// saving it unchanged must PUT the same input/expected the server holds; otherwise content_hash changes (the case
// silently leaves the baseline comparison, D8) and a dict `expected` degrades from a field match to a substring check.
// Run: `npm test` (node --test; Node >= 22.6 strips the TS types of adapters.ts natively — no test framework).
import { test } from "node:test";
import assert from "node:assert/strict";
import { caseToEvalCaseIn, evalCaseOutToCase } from "../src/lib/api/adapters.ts";

const OUT = {
  id: "c1", suite_id: "s1", content_hash: "h", updated_at: "2026-11-18T00:00:00Z",
  input: { message: "An XDR alert fired", count: 3, ctx: { host: "db-1" }, region: "eu" },
  rubric: [{ name: "accuracy", weight: 1 }],
  required_fields: ["severity"],
  expected: { severity: "high" },
};

test("an unchanged case round-trips to the exact server input and expected", () => {
  const back = caseToEvalCaseIn(evalCaseOutToCase(OUT));
  assert.deepEqual(back.input, OUT.input);
  assert.deepEqual(back.expected, OUT.expected);
  assert.deepEqual(back.required_fields, OUT.required_fields);
});

test("an edited JSON expected is sent back as JSON, not as a string", () => {
  const c = evalCaseOutToCase(OUT);
  const back = caseToEvalCaseIn({ ...c, expected: '{"severity":"low"}' });
  assert.deepEqual(back.expected, { severity: "low" });
});

test("an edited non-string var is sent back as its JSON value", () => {
  const c = evalCaseOutToCase(OUT);
  const back = caseToEvalCaseIn({ ...c, vars: { ...c.vars, count: "4" } });
  assert.equal(back.input.count, 4);
  assert.equal(back.input.region, "eu");
});

test("string values stay strings, edited or not", () => {
  const c = evalCaseOutToCase({ ...OUT, expected: "restart" });
  assert.equal(caseToEvalCaseIn(c).expected, "restart");
  assert.equal(caseToEvalCaseIn({ ...c, expected: "42" }).expected, "42");
  assert.equal(caseToEvalCaseIn({ ...c, vars: { ...c.vars, region: "123" } }).input.region, "123");
});

test("a console-authored case (no server origin) sends plain strings", () => {
  const back = caseToEvalCaseIn({ id: "n", input: "hi", vars: { region: "{}" }, assertions: [],
    rubric: [{ name: "accuracy", weight: 1 }], expected: "[1]" });
  assert.deepEqual(back.input, { message: "hi", region: "{}" });
  assert.equal(back.expected, "[1]");
});
