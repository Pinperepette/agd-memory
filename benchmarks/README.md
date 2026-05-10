# agd-memory benchmarks

Benchmarks for the **addressable memory layer** behind the
[agd-memory](../README.md) plugin. The question this folder answers
is not "does AGD beat markdown on token count": it is **"when I can
address a memory block by name, how much do I save versus loading
the whole file?"**

Five complementary measures:

1. **Tokens shipped** (S0–S5): how many tokens enter the prompt.
2. **Backlink fan-in** (S6): when the graph turns against you.
3. **Real dollars** (S7): actual Anthropic API calls, prompt cache
   on, Haiku 4.5.
4. **Subagent execution** (S8): real Claude Code subagents on a real
   skill, monolithic preload vs AGD addressable retrieval. Measures
   tokens, tool calls, wall-clock, branch precision, and correctness.
5. **CLI latency**: parsing overhead of `agd` versus raw I/O.

The numbers come from real execution. No formulas, no mocking:
`agd` is invoked as a subprocess and its stdout is tokenized; for
S7, `messages.create` is called for real and `usage` is read off
the response; for S8, subagents are spawned via Claude Code's
`Agent` tool and the runtime telemetry is logged.

## TL;DR

On a synthetic 200-block corpus (~18k tokens whole-doc):

| metric | result | section |
|---|---:|---|
| TOC + 1 block vs whole-doc, in tokens | **7.9× cheaper** | §S1 |
| 10 agents shared-TOC vs naive, in tokens | **45.7× cheaper** | §S2 |
| `--kind x-project` filter, in tokens | **25.1× cheaper** | §S4 |
| Selective vs whole-doc cached, in **real dollars** (5-turn Haiku) | **2.15× cheaper** | §S7 |
| Backlink explosion: anchor with 200 fan-in | **81% of the file** | §S6 |
| Subagent A/B on a real skill (4 tasks, monolithic preload vs AGD) | **−40.6% tokens, parity correctness** | §S8 |

**Three caveats to read before quoting any number:**

1. **On the real file** (31 blocks, MDF2) the TOC/whole ratio is
   **6.4×**, not 8×. Synthetic corpora have zero variance — real
   ones have long `desc=` fields that inflate the TOC. See §S0.
2. **Tokens ≠ dollars.** S3 reports 3.4× in raw tokens; with real
   prompt caching the dollar advantage drops to **2.15×** (§S7).
   Always state which axis you are quoting.
3. **Multi-agent is not free.** The 45.7× of S2 requires the
   parent to orchestrate slices explicitly. Default Claude Code
   subagents run "independent" (~7.5×), not "shared" (45×).

## S0 — synthetic vs real

| corpus | blocks | whole-doc | TOC | ratio |
|---|---:|---:|---:|---:|
| synthetic mem-50 | 50 | 4,446 | 541 | **8.2×** |
| real memory.agd (MDF2) | 31 | 4,790 | 748 | **6.4×** |

The synthetic is ~28% more optimistic. When publishing a number
measured on real files, **5–7×** is the honest band at this size.

## S1 — single agent, four sizes

| blocks | whole-doc | TOC | TOC + 1 | TOC + 3 | TOC ratio | TOC+3 ratio |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 915 | 104 | 172 | 381 | 8.8× | 2.4× |
| 50 | 4,446 | 541 | 650 | 770 | 8.2× | 5.8× |
| 200 | 18,245 | 2,233 | 2,313 | 2,535 | 8.2× | 7.2× |
| 1,000 | 90,428 | 11,152 | 11,246 | 11,394 | 8.1× | **7.9×** |

The bigger the file, the better selective retrieval looks. At 1k
blocks, fetching one specific block on top of the TOC costs 0.18%
more than the TOC alone.

## S2 — N parallel agents

200-block corpus. Each agent picks 1–3 random blocks.

| agents | naive | independent | shared TOC | shared vs naive |
|---:|---:|---:|---:|---:|
| 1 | 18,245 | 2,346 | 2,346 | 7.8× |
| 3 | 54,735 | 7,247 | 2,781 | 19.7× |
| 5 | 91,225 | 12,086 | 3,154 | 28.9× |
| 10 | 182,450 | 24,093 | 3,996 | **45.7×** |
| 20 | 364,900 | 48,217 | 5,790 | **63.0×** |

**Architectural note.** "Shared TOC" = the parent reads the TOC
once and dispatches only the relevant block slices to each child.
**This is not Claude Code's default.** When you launch the `Agent`
tool, each child gets its own context and runs retrieval
independently — so out of the box you sit on the "independent"
row (7.5×), not the "shared" row (45×). Reaching 45× requires
hand-written parent-child orchestration.

## S3 — multi-turn session (raw tokens)

20 turns on the same 200-block corpus, each turn needing 1–2
different blocks.

| turns | whole-doc kept | selective cumulative | per-turn avg | token ratio |
|---:|---:|---:|---:|---:|
| 20 | 18,245 | 5,297 | 153 | **3.4×** |

**In raw tokens** selective wins 3.4×. **In real dollars** it wins
2.15× (see §S7). Always quote the right metric for the question.

## S4 — `--kind` scoping

| context | kinds | whole-doc | scoped TOC | ratio |
|---|---|---:|---:|---:|
| blog-write | `x-user,x-feedback` | 18,245 | 1,157 | 15.8× |
| code-review | `x-feedback,x-reference` | 18,245 | 1,340 | 13.6× |
| project-status | `x-project` | 18,245 | 726 | **25.1×** |

`--kind` filtering is the cheapest way to scope a session before
even looking at content.

## S5 — addressable memory: what does it cost?

The pitch for AGD is not "replace markdown". It is: **when you
need to call a memory block by name and get it isolated from the
rest, what does that capability cost?**

Same content as the 200-block corpus, three representations:

| format | whole-doc | id-list | selective fetch | addressable? |
|---|---:|---:|---:|:---:|
| **Markdown** | **14,342** | — | 14,342 (= whole) | ✗ |
| **AGD** | 18,245 | 2,233 | 2,340 | ✓ |
| **XML** | 20,163 | 2,229 | 2,345 | ✓ |

**The honest reading.** Markdown is **27% smaller** on whole-doc
load — it is the winning format *if* the memory fits in context
and you do not need to address it. Past a certain size that breaks:
without per-block IDs, the only strategy is loading everything.

AGD and XML are both addressable. The gap between them is **10.5%
on whole-doc** (tag overhead) and **negligible on selective fetch**
(~5 tokens out of ~2,300). AGD's token edge over XML is marginal —
the actual difference is **human ergonomics**: line prefix + ID in
brackets vs open/close tags everywhere.

**So: AGD does not beat markdown on tokens. It beats "non-addressable
memory". Markdown stops being an alternative once the file no
longer fits in context.**

## S6 — backlink explosion

`agd get '#anchor' --with-backlinks` returns the anchor plus every
block declaring `refs="#anchor"`. Useful pattern, but it scales
linearly in fan-in. What happens on a popular anchor?

| inbound refs | anchor alone | anchor + backlinks | fan-in vs anchor | % of whole-doc |
|---:|---:|---:|---:|---:|
| 5 | 33 | 505 | 15× | **3%** |
| 50 | 33 | 4,804 | 146× | **26%** |
| 200 | 33 | 19,246 | 583× | **81%** |

At 200 backlinks you are loading the file via the side door. The
"selective" advantage is gone.

**Practical implication.** High-fan-in anchors need a `--limit` or
pagination mechanism (currently absent). Without it, the "scope
query with `--with-backlinks`" pattern becomes dangerous as the
memory grows and certain blocks become hubs.

## S7 — real money via Anthropic API

`scripts/bench_cost.py` makes real API calls to `claude-haiku-4-5`
with prompt caching on, records `cache_creation_input_tokens`,
`cache_read_input_tokens`, `input_tokens`, `output_tokens` from
the response `usage` field, and prices them at the published list
(input $1/M, cache write $1.25/M, cache read $0.10/M, output
$5/M).

5 turns on the 200-block corpus, same questions on both sides:

### Strategy A — whole-doc cached

| turn | input | cache write | cache read | output | $ |
|---:|---:|---:|---:|---:|---:|
| 1 | 45 | **20,041** | 0 | 120 | $0.0257 |
| 2 | 45 | 0 | 20,041 | 119 | $0.0026 |
| 3 | 45 | 0 | 20,041 | 120 | $0.0026 |
| 4 | 45 | 0 | 20,041 | 120 | $0.0026 |
| 5 | 45 | 0 | 20,041 | 120 | $0.0026 |
| **total** | 225 | 20,041 | 80,164 | 599 | **$0.0363** |

Average latency: **1.85 s/turn**.

### Strategy B — selective uncached

| turn | input | output | $ |
|---:|---:|---:|---:|
| 1 | 2,923 | 66 | $0.0033 |
| 2 | 2,922 | 98 | $0.0034 |
| 3 | 2,882 | 86 | $0.0033 |
| 4 | 2,931 | 119 | $0.0035 |
| 5 | 2,876 | 97 | $0.0034 |
| **total** | 14,534 | 466 | **$0.0169** |

Average latency: **1.87 s/turn**.

### The honest verdict

| dimension | whole-doc cached | selective uncached | ratio |
|---|---:|---:|---:|
| tokens shipped (visible to model) | 100,205 | 14,534 | 6.9× |
| **real dollars** | $0.0363 | $0.0169 | **2.15×** |
| average latency | 1.85 s | 1.87 s | ≈ |

The prompt cache **claws back ~70%** of the token-level advantage.
In dollars, selective still wins, but by just over 2× — not by 7×.

**Crossover.** Linearly extrapolating (one write, constant reads,
linear selective), Strategy A becomes cheaper than B around
**turn ~30**, *as long as the cache stays warm*. Default cache TTL
is 5 minutes: if turn intervals exceed 5 min, A pays the write
again (~$0.025) and B wins indefinitely.

**Latency.** Identical within noise. For Haiku 4.5 at these prompt
sizes, time is dominated by output generation, not input
processing. On Sonnet/Opus or much larger contexts (>50k tokens)
the gap would show up.

## S8 — skill routing: monolithic preload vs AGD routed retrieval

S0–S7 measure memory operations in isolation. S8 measures **what
happens when an agent actually executes against the memory**.

Setup: take an upstream Claude Code skill
([`anthropics/skills/claude-api`](https://github.com/anthropics/skills/tree/main/skills/claude-api),
33 KB SKILL.md = ~7,948 tokens), decompose it into 38 AGD blocks,
then run the same four tasks through two arms — monolithic preload
(read SKILL.md upfront) vs AGD routed (3-block skeleton + on-demand
fetch from a sidecar `.agd` file).

| arm | total tokens (4 tasks) | tool calls | wall-clock | tasks correct |
|---|---:|---:|---:|---:|
| Monolithic | 117,002 | 16 | 211.6 s | 4/4 |
| AGD routed | 69,545 | 10 | 124.1 s | 4/4 |
| **delta** | **−40.6%** | **−37.5%** | **−41.4%** | parity |

Headline non-token finding: **task B finished with zero block
fetches**. The router's navigation map names
`#claude-api-pitfall-migration-scope` by ID with a one-line
description, and the name alone was sufficient to trigger the
correct behavior (ask scope before editing). The router itself
encodes operational knowledge — not just an index.

Caveats: N=1 per cell (4 tasks × 2 arms × 1 rep = 8 subagent runs).
Strong directional signal, weak statistical power. The skill picked
decomposes cleanly along two near-orthogonal axes (language ×
feature); a denser cross-referenced skill would show smaller savings.

Full report, exact prompts, decomposed sidecar, and raw telemetry:
[`skill_routing/`](skill_routing/).

## Concurrency model — what AGD means by "multi-agent safe"

To prevent wrong expectations after reading S2:

- **Atomic writes**: every edit is written to a tempfile and
  renamed in. The file is never half-corrupted.
- **`flock` advisory**: concurrent writes are serialized by an OS
  lock. There is no merge — there is a queue.
- **Last-write-wins per ID**: if two agents write the same block,
  whichever gets the lock last wins. The first write is lost
  silently, with no conflict marker.

What AGD is **not**:

- ✗ CRDT
- ✗ automatic merge across diverging versions
- ✗ distributed consistency
- ✗ vector clocks / causal ordering

If you need to coordinate K agents editing the same blocks
simultaneously, AGD alone is not enough — you need an upstream
scheduler.

## CLI latency

Median of 20 runs on the 1,000-block corpus (~310KB):

| operation | time |
|---|---:|
| `agd ids` | 22.5 ms |
| `agd ids --kind` | 21.4 ms |
| `agd get` 1 block | 21.0 ms |
| `cat` (I/O baseline) | 16.7 ms |

`agd` adds ~5 ms of parsing on top of I/O. Negligible compared to
an LLM round-trip (1–5 s).

## Known limitations

- **Synthetic corpora**. Bodies are drawn from a fixed pool of 8
  lorem strings. Variance ~30% lower than real files. Ratios are
  optimistic by the same amount.
- **`tiktoken` ≠ Claude tokenizer**. Stable proxy in ratio, not in
  absolute count. S7 uses the API directly: those numbers are
  exact.
- **S7 is on Haiku 4.5**. On Sonnet/Opus the ratios would change:
  higher base cost makes every saved token more valuable. On
  models without prompt caching, Strategy A always loses.
- **Simulated multi-agent (S2)**. Counts tokens as if each agent
  were an independent job. No simulation of framework overhead,
  dispatch latency, or subagent serialization.
- **Idealized XML (S5)**. Compact schema. Realistic HTML (`<div
  class="block">`) would be ~30% more verbose and would tilt the
  comparison further away from AGD.
- **Backlink explosion not mitigated (S6)**. `agd` lacks `--limit`
  or pagination; the `--with-backlinks` pattern offers no
  protection against high-fan-in hubs.

## Reproducing

```sh
cd benchmarks
pip install tiktoken anthropic

# token-only scenarios (S0–S6, latency)
python3 scripts/generate.py --out corpora/mem-200.agd --blocks 200 --seed 42
python3 scripts/generate.py --out corpora/anchor-fanin-050.agd \
  --blocks 200 --anchor-inbound 50 --seed 42
python3 scripts/bench.py --real-memory ~/path/to/your/memory.agd

# real-money scenario (S7) — about $0.05 per run with defaults
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/bench_cost.py --turns 5

cat results/summary.json results/cost.json
```

All synthetic corpora are bit-for-bit reproducible (`--seed 42`).

## Layout

```
benchmarks/
├── README.md                  # this file
├── corpora/                   # synthetic .agd, deterministic
│   ├── mem-{10,50,200,1000}.agd
│   └── anchor-fanin-{005,050,200}.agd
├── results/
│   ├── summary.json           # S0–S6 + latency output
│   └── cost.json              # S7 output (Anthropic API)
├── scripts/
│   ├── generate.py            # corpus generator
│   ├── bench.py               # token-accounting harness
│   └── bench_cost.py          # real-money harness
└── skill_routing/             # S8 — subagent A/B on a real skill
    ├── README.md              # full report
    ├── corpus/                # SKILL.md snapshot + decomposed .agd
    ├── prompts/               # verbatim subagent prompts (8 cells)
    └── results/               # raw telemetry per cell
```
