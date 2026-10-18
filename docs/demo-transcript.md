# ELT video — Raman's ~4-min segment (TTS voiceover script)

**How to use this.** Record the screen on your laptop following the `[VISUAL]` / `[DEMO]` cues. Generate the
voice by pasting the **Spoken-only block** (bottom) into your TTS tool, then lay the voice over the screen
recording. Target ~4 min 30 s at ~140 words per minute (~630 words). Voice = organizational narrator
("our teams… we"), neutral-confident. **Framing: Agent Platform *aligns with* the AI Champions program — it
is not part of it and we are not selling the program.** Every claim traces to `ai-champions-alignment.md`.

Open-in-advance tabs for the demo (all mock, real screens): `/agents/[id]` (Configure · Knowledge ·
Tools & MCP · Evaluations · Publish), the role switcher ("Preview as role"), `/channels`.

---

## Two-track script

### Beat 1 — Stakes · ~45s · SLIDE 1 (title) → SLIDE 2 (stakes)
`[VISUAL: Slide 1 title holds ~3s, advance to Slide 2 "the rules exist; nothing enforces them"]`

**SPOKEN:** Across Trellix, our teams are building AI agents faster than ever. That is the win. Here is the
risk. Right now there are around forty of them, shipping with almost no enforcement behind them. The AI
Champions Playbook already sets the rules — register every agent, reuse before you build, prove quality with
evaluations, cap cost, run least privilege. But those rules live in a document. Nothing makes them stick. So
we carry real exposure: duplicated agents nobody caught, spend with no ceiling, and the quiet one that
matters most — an agent whose quality silently drifts after a model update, where the first people to notice
are our customers.

### Beat 2 — The answer (aligns with the playbook) · ~40s · SLIDE 3
`[VISUAL: Slide 3 — "the governance layer that makes the playbook's rules stick"]`

**SPOKEN:** This is where Agent Platform comes in. It aligns directly with that playbook — it is the
governance layer that makes those rules actually stick. Think of it as the control plane every agent passes
through on its way to production: if an agent is not registered, evaluated, and scoped, it does not ship. It
is not the program itself — it is how the program's principles get enforced in software. And it is real
today: a working prototype of the full experience, with the one genuinely hard part, the evaluation and
drift core, as our focused engineering investment.

### Beat 3 — Build a governed agent (SLIDE 3 + live demo) · ~60s
`[VISUAL: Slide 3 stays up — the build 4-grid (Knowledge · Tools · Vetted MCP · Eval gate); then cut to the live console for the DEMO below]`
`[DEMO, in order:]`
- `[/agents/[id] Configure → model + instructions]`
- `[Knowledge tab → attach a knowledge base (ground answers in approved docs)]`
- `[Tools & MCP tab → bind the tools it needs + connect a vetted, existing MCP server (e.g. Splunk / VirusTotal)]`
- `[Evaluations / Publish → Incident Report Writer failing run: quality 78.4, −9.2 vs baseline, verdict FAIL, trigger "Model swap (gpt-5)"; publish blocked]`

**SPOKEN:** Building a governed agent takes minutes, not a sprint. You start from a template, choose a model,
and write the instructions. Then you ground it — attach a knowledge base, so its answers come from approved
content, not guesswork. You give it capabilities — bind the specific tools it needs, and connect only
vetted, existing MCP servers; nobody spins up a new one. Every tool and data source the agent can touch is
explicit and governed. And then the part that matters most — you prove it. No agent can publish without a
passing evaluation against a frozen baseline. This incident report writer just failed its gate — quality
dropped nine points after a model swap — so the platform blocks the release. A human decides; drift doesn't
slip through silently.

### Beat 4 — Who can do what: RBAC + cost & model governance · ~55s · SLIDE 4 + live demo
`[VISUAL: Slide 4 (RBAC); then DEMO: "Preview as role" switcher — as Builder the Publish button is gated; as Publisher it's allowed; as Admin show access/keys + the agent's per-agent budget, token caps, and allowed-model setting (Configure / Access tabs)]`

**SPOKEN:** Of course, not everyone should be able to do everything — so access is role-based and least
privilege by default. A builder can create and configure agents, but cannot publish to production. Only a
publisher can publish. And only an admin holds the governance levers: managing access and keys, and
controlling cost — setting per-agent budgets and token caps, and governing which models an agent is even
allowed to use. These roles come straight from Trellix IAM, so there is no second identity system to manage,
and no agent can quietly run up spend on an expensive model.

### Beat 5 — Reach: how a non-technical person uses an agent · ~50s · SLIDE 5 + `/channels`
`[VISUAL: Slide 5 (channels); then the /channels screen showing Slack, Teams, Google Chat connections]`

**SPOKEN:** Now, how does the rest of the company actually use these agents? A non-technical employee never
touches an API or a command line. They simply talk to the agent where they already work — in Slack,
Microsoft Teams, or Google Chat. Behind the scenes, their identity is federated from Okta, and two gates
decide what they can reach: whether they are allowed on that channel at all, and what each agent's access
policy permits. So a published agent can safely reach the entire company, and every person sees only the
agents they are cleared to use.

### Beat 6 — Close + compact CTA · ~35s · SLIDE 6
`[VISUAL: Slide 6 — close line, wordmark, alignment CTA]`

**SPOKEN:** So that is the full loop — register, prove, govern, and reach — the rules the playbook asks for,
enforced in one place, so Champions can run the whole lifecycle from a single console instead of hand-wiring
glue. Agent Platform aligns with the AI Champions Playbook, and it is how those principles get enforced in
practice. The ask is simple: adopt it as the place agents are registered, proven, and governed — and let us
prove it on one area first.

---

## Spoken-only block (paste into TTS)

> One paragraph break per beat; no stage directions. ~590 words, ~4 min 10 s at 140 wpm.

Across Trellix, our teams are building AI agents faster than ever. That is the win. Here is the risk. Right now there are around forty of them, shipping with almost no enforcement behind them. The AI Champions Playbook already sets the rules — register every agent, reuse before you build, prove quality with evaluations, cap cost, run least privilege. But those rules live in a document. Nothing makes them stick. So we carry real exposure: duplicated agents nobody caught, spend with no ceiling, and the quiet one that matters most — an agent whose quality silently drifts after a model update, where the first people to notice are our customers.

This is where Agent Platform comes in. It aligns directly with that playbook — it is the governance layer that makes those rules actually stick. Think of it as the control plane every agent passes through on its way to production: if an agent is not registered, evaluated, and scoped, it does not ship. It is not the program itself — it is how the program's principles get enforced in software. And it is real today: a working prototype of the full experience, with the one genuinely hard part, the evaluation and drift core, as our focused engineering investment.

Building a governed agent takes minutes, not a sprint. You start from a template, choose a model, and write the instructions. Then you ground it — attach a knowledge base, so its answers come from approved content, not guesswork. You give it capabilities — bind the specific tools it needs, and connect only vetted, existing MCP servers; nobody spins up a new one. Every tool and data source the agent can touch is explicit and governed. And then the part that matters most — you prove it. No agent can publish without a passing evaluation against a frozen baseline. This incident report writer just failed its gate — quality dropped nine points after a model swap — so the platform blocks the release. A human decides; drift doesn't slip through silently.

Of course, not everyone should be able to do everything — so access is role-based and least privilege by default. A builder can create and configure agents, but cannot publish to production. Only a publisher can publish. And only an admin holds the governance levers: managing access and keys, and controlling cost — setting per-agent budgets and token caps, and governing which models an agent is even allowed to use. These roles come straight from Trellix IAM, so there is no second identity system to manage, and no agent can quietly run up spend on an expensive model.

Now, how does the rest of the company actually use these agents? A non-technical employee never touches an API or a command line. They simply talk to the agent where they already work — in Slack, Microsoft Teams, or Google Chat. Behind the scenes, their identity is federated from Okta, and two gates decide what they can reach: whether they are allowed on that channel at all, and what each agent's access policy permits. So a published agent can safely reach the entire company, and every person sees only the agents they are cleared to use.

So that is the full loop — register, prove, govern, and reach — the rules the playbook asks for, enforced in one place, so Champions can run the whole lifecycle from a single console instead of hand-wiring glue. Agent Platform aligns with the AI Champions Playbook, and it is how those principles get enforced in practice. The ask is simple: adopt it as the place agents are registered, proven, and governed — and let us prove it on one area first.

---

## Combined-reel handoff note (Kanan first → Raman second)

- Kanan opens the reel; Raman's segment (~4:10) follows and closes it.
- **Opener bridge (recommended):** replace Beat 1's first two sentences with a bridge from Kanan's topic
  (confirm at the 1:1), e.g. *"Kanan just showed [X]. Here is the governance layer that keeps all of that safe
  to scale."* — then continue from "Right now there are around forty of them…".
- Raman's Beat 6 close ends the whole reel; no further handoff needed.
- If the combined cut runs long, Beat 3 can drop the knowledge/MCP sentence (saves ~12s) without breaking the argument.
