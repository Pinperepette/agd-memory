# §S8 — Skill Routing: Monolithic Preload vs AGD Addressable Retrieval

This benchmark answers a question the §S0–§S7 series in
[../README.md](../README.md) doesn't: **when execution quality enters the
picture**, does AGD addressable retrieval hold up against monolithic preload?

§S0–§S7 measure token shipping, backlink fan-in, and real dollars on memory
operations. §S8 measures real subagent **execution** on a real Claude Code
skill. Same question family — different layer.

The setup: take an upstream Claude Code skill
([`anthropics/skills/claude-api`](https://github.com/anthropics/skills/tree/main/skills/claude-api),
33 KB SKILL.md), decompose it into 38 AGD blocks, then run the same four tasks
through two arms:

- **monolithic** — subagent reads the full SKILL.md upfront (the default skill
  loading model).
- **AGD routed** — subagent starts with a 3-block skeleton already in context;
  the rest of the graph is in a sidecar `.agd` file, fetched by ID via
  `agd get` only when needed.

Eight subagent runs total. Telemetry from Claude Code's `Agent` tool
(`total_tokens`, `tool_uses`, `duration_ms`); self-reported pitfalls and
fetched blocks from each subagent's mandated final report.

## TL;DR

| metric                                                | monolithic | AGD routed | delta      |
| ----------------------------------------------------- | ---------: | ---------: | ---------: |
| aggregate tokens (4 tasks)                            |    117,002 |     69,545 | **−40.6%** |
| aggregate tool calls                                  |         16 |         10 | **−37.5%** |
| aggregate wall-clock                                  |    211.6 s |    124.1 s | **−41.4%** |
| tasks producing correct output                        |      4 / 4 |      4 / 4 |     parity |
| best-case branch precision (tasks B, D)               |          — | **0–1** fetches | n/a   |
| worst-case branch precision (task C, complex code)    |          — | 7 blocks / **2** fetch calls | n/a |

**Caveats to read before quoting any number** (in order of importance):

1. **N=1 per cell.** Eight subagent runs (4 tasks × 2 arms × 1 rep). Strong
   directional signal, weak statistical power. Replications welcome.
2. **Self-reported tool counts** are within ±1 of telemetry — agents
   under-count failed Writes. Token totals come from the harness, not from the
   agent.
3. **The skill picked decomposes well.** `claude-api` is structured along two
   nearly-orthogonal axes (language × feature). A skill with denser
   cross-references between sections would show smaller savings.
4. **Quality scoring is mine, not blind.** A monolithic agent surfaced "Opus
   pricing jump" on the migration task; the AGD agent didn't. Whether that
   counts as quality loss depends on what you weight (see §Quality differences).

## Setup

### Skill chosen

`anthropics/skills/claude-api` from
[github.com/anthropics/skills](https://github.com/anthropics/skills).

- 33 KB SKILL.md = **7,948 tokens** of monolithic preload (`agd bench`,
  `cl100k_base`).
- 8 language sub-folders (Python, TypeScript, Java, Go, Ruby, C#, PHP, cURL).
- `shared/` with caching, tool-use, agent-design, model-migration, error-codes,
  live-sources.

The Reading Guide section of the SKILL.md is, in effect, a router written in
prose ("if user wants chat UI → read README.md + streaming.md"). The
decomposition turns that prose router into an executable one.

### Decomposition

SKILL.md split into **38 AGD blocks** in the sidecar
[`corpus/skill-claude-api.agd`](corpus/skill-claude-api.agd) (5,792 tokens
total). Validated with `agd validate`, 0 broken refs (`agd ref`). Block
taxonomy:

| layer                    | count | role                                                               |
| ------------------------ | ----: | ------------------------------------------------------------------ |
| always-run (skeleton)    |     3 | router + 2 universally-applied gates (model defaults, SDK rule)    |
| lookup / decision        |    13 | language detect, surface table, models-current, thinking-table, …  |
| pitfall (tagged)         |     7 | `#…-pitfall-thinking-47`, `-max-tokens`, `-migration-scope`, …     |
| reference stub (CDN ptr) |    15 | per-language folders + `shared/*.md` files, body lives upstream    |

The 3 always-run blocks are saved into `~/.claude/projects/.../memory.agd` as
`x-reference` entries (the user's project memory). Cost: **1,226 tokens**
added to the bootstrap surface, present on every session start. The rest
(31 blocks, ~4,566 tokens) lives in the sidecar `.agd` file; the agent fetches
by ID via the AGD CLI when it actually needs them.

### Two arms

Both arms share the task prompt and project context. They differ only in how
the skill is delivered.

**Monolithic preload.** Subagent prompt instructs:
> START by reading `/tmp/claude-api-skill/SKILL.md` in full with the Read tool.
> This simulates monolithic preload. Do NOT use WebFetch. Do NOT read any
> .agd files.

This costs one Read of 7,948 tokens at session start. Skill content is in
context; no further fetches needed.

**AGD routed.** Subagent prompt inlines the 3 skeleton blocks (1,226 tokens),
then says:
> Full graph (38 blocks) at: `/Users/…/skill-claude-api.agd`
> Fetch via: `~/.cargo/bin/agd get <path> '#<id>'`
> Do NOT Read the .agd file directly — fetch via the agd CLI only.
> Do NOT Read the monolithic SKILL.md.
> Do NOT use WebFetch.
> Fetch only what you actually need.

The skeleton router lists every available block ID with a one-line
description, so the agent knows what's available without fetching anything.

### Tasks

Chosen to span the workflow shape of a Claude API project — implementation,
migration, complex feature, debug.

| task | language   | brief                                                                                              | tests for                                                       |
| ---- | ---------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| A    | Python     | "write app.py with prompt caching + streaming, ~3000-char system prompt"                           | implementation happy path; defaults; one feature                |
| B    | Python     | user says "migrate my codebase to Opus 4.7" against a project of ~30 .py files. First action?      | does the agent surface the buried "ASK scope first" pitfall?    |
| C    | TypeScript | "add tool-use loop with `get_weather` + stream the final response"                                 | language switch; multi-feature (tool use + streaming); deep refs |
| D    | Python     | "I added cache_control but cache_read_input_tokens=0" + buggy code with `datetime.now()` in prefix | debug; deep caching pitfall surface                             |

Tasks A, C produce code. B, D produce diagnoses. Mix of "happy path
implementation" and "deep pitfall surface."

## Results

### Aggregate

| arm        | total tokens | tool calls | duration | tasks correct |
| ---------- | -----------: | ---------: | -------: | ------------: |
| Monolithic |      117,002 |         16 |  211.6 s |         4 / 4 |
| AGD routed |       69,545 |         10 |  124.1 s |         4 / 4 |
| **delta**  |  **−40.6%**  | **−37.5%** | **−41.4%** |     parity |

### Per-task

| cell | task                       | arm        | tokens |  tool calls | duration | correct |
| ---- | -------------------------- | ---------- | -----: | ----------: | -------: | :-----: |
| A1   | Python — caching           | monolithic | 28,546 |           2 |   47.7 s |    ✓    |
| A2   | Python — caching           | AGD routed | 19,098 |           3 |   40.1 s |    ✓    |
| B1   | migration scope            | monolithic | 26,625 |           1 |   18.9 s |    ✓    |
| B2   | migration scope            | AGD routed | 14,786 |       **0** |    9.8 s |    ✓    |
| C1   | TS — tool loop + stream    | monolithic | 34,653 |          10 |  118.6 s |    ✓    |
| C2   | TS — tool loop + stream    | AGD routed | 20,517 |           6 |   55.2 s |    ✓    |
| D1   | debug cache hit            | monolithic | 27,178 |           3 |   26.4 s |    ✓    |
| D2   | debug cache hit            | AGD routed | 15,144 |           1 |   19.0 s |    ✓    |

Per-task token reductions: **A −33.1% · B −44.5% · C −40.8% · D −44.3%**.

### Branch precision

The headline non-token finding. Monolithic agents have no concept of "branch":
the entire skill enters context at start. AGD agents make explicit retrieval
decisions. Across the four tasks:

| task | AGD blocks fetched                                                                                                                                                          | fetch calls | precision verdict                                                          |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------: | -------------------------------------------------------------------------- |
| A    | `#claude-api-caching-quick`                                                                                                                                                 |           1 | high — bootstrap covered model/thinking/streaming                          |
| B    | (none)                                                                                                                                                                      |       **0** | maximal — block name in router was sufficient                              |
| C    | `#task-router`, `#lang-features`, `#ref-…-typescript`, `#ref-…-shared-tool-use-concepts`, `#pitfall-tool-json-parsing`, `#pitfall-thinking-47`, `#pitfall-max-tokens`        |           2 | high — fetched the right pitfalls + the TypeScript ref + the concept stub  |
| D    | `#claude-api-caching-quick`                                                                                                                                                 |           1 | maximal                                                                    |

Two phenomena worth naming:

1. **Bootstrap coverage.** The 3 always-run blocks (router + model defaults +
   SDK rule) cover the universal vocabulary of Claude API code. Tasks A and C
   never fetched defaults; they were already in context. The monolithic arm
   paid for that vocabulary every time, in a 7,948-token slab.

2. **Names as semantics.** Task B finished with **zero fetches**. The router's
   navigation map names `#claude-api-pitfall-migration-scope` as a known block
   — and that name alone, with a one-line description, was enough for the
   agent to apply the rule (ask scope before editing). The agent did not need
   to read the body. **The router itself encodes operational knowledge — not
   just an index.**

### Quality differences (where the arms diverge)

Token math says AGD wins. Quality is more nuanced. Two patterns:

- **Monolithic surfaced more pitfalls per task** (mean 5.75 vs 3.5). Some were
  over-applied — "no assistant prefill" was named on tasks where prefill
  wasn't being used. This correlates with how much material is in front of
  the agent: more context → more cross-reference → more "thoroughness," some
  noise.
- **Monolithic added value the AGD missed twice:**
  - Task B (B1): cited the **Opus pricing jump** ($5/$25 per 1M tokens) as a
    migration concern. Worth raising; the AGD agent didn't fetch
    `#claude-api-models-current` (where pricing lives) and so didn't know.
  - Task D (D1): noted the **~1024-token minimum cacheable prefix** as a
    secondary risk after fixing the volatility bug. This detail lives in
    `shared/prompt-caching.md` (a CDN ref); the AGD agent fetched only the
    quick-reference block and missed it.

The discipline tradeoff is real: routed retrieval produces narrower, faster,
more focused responses; preloaded context produces broader, slower, more
exhaustive ones. **Neither dominates outside specific use cases.** If you
want every adjacent concern raised proactively, monolithic wins. If you want
the agent to do exactly what was asked and stop, AGD wins.

### Wall-clock observation

AGD-routed runs finished in roughly half the wall-clock time of their
monolithic counterparts (124.1 s vs 211.6 s aggregate). The AGD arm reads
less and decides faster; the monolithic arm reads ~8K tokens upfront before
producing anything.

## Implications

### For subagents specifically

The case for addressable decomposition is strongest in the subagent setting.
A subagent:

- inherits no parent context (each spawn is a fresh prompt),
- runs a focused task (won't reuse the skill across many turns),
- pays for every token of the skill on every spawn.

Across our 4 tasks, AGD routing saved **47,457 tokens** — roughly 6 full
SKILL.md preloads' worth. On a project that spawns subagents at any volume,
that compounds linearly.

This connects to a frame from the design conversation that produced this
benchmark:

> The subagents today read the router. With AGD, they execute it.

A monolithic SKILL.md is a router *and* its destinations, fused into one slab
of text that the agent must read linearly. An AGD-decomposed skill separates
the two: the router is small and always loaded; the destinations are
addressable and fetched on demand. Subagents pay only for the route they
actually walk.

### For interactive sessions

The tradeoff is less clear. In a long interactive session the same skill gets
hit many times; the upfront preload amortizes; the over-fetch problem
shrinks. AGD routing still saves on the worst-case "skill not needed at all"
sessions (where the skeleton is wasted but smaller than the monolithic
preload), but the per-task win narrows.

### What we can't claim from N=1

- A statistically significant difference. The signal is consistent across all
  4 tasks (every cell shows ≥30% token reduction with parity correctness),
  but 4 ≠ a distribution.
- That AGD always wins on quality. We saw two cases where monolithic
  surfaced extra value (Opus pricing, 1024-token minimum). Those are real
  losses for AGD that fairer routing — or richer skeleton — could close.

## Reproducibility

- **Skill snapshot.** [`corpus/claude-api-SKILL.md`](corpus/claude-api-SKILL.md)
  — verbatim copy of `anthropics/skills/claude-api/SKILL.md` as of
  2026-05-10.
- **Decomposed graph.**
  [`corpus/skill-claude-api.agd`](corpus/skill-claude-api.agd) — 38 blocks,
  validates with `agd validate`, `agd ref` returns no broken edges.
- **Exact prompts.** `prompts/{A,B,C,D}{1,2}_*.md` — one file per cell. The
  monolithic and AGD prompts share task wording verbatim; only the
  skill-access block differs.
- **Raw telemetry.**
  [`results/run-2026-05-10.json`](results/run-2026-05-10.json) — total
  tokens, tool uses, duration, correctness, blocks fetched, pitfalls listed,
  per cell.
- **Re-running.** Prompts assume Claude Code with the `Agent` tool
  available. Cells run independently; they don't share state. C1/C2 must use
  separate working directories (provided in the prompts) to avoid file
  conflicts on the produced `app.ts`.

---

*This benchmark is part of the agd-memory benchmarks suite. See sibling
sections §S0–§S7 in [`../README.md`](../README.md) for token-math benchmarks
on synthetic and real corpora.*
