---
name: memory
description: "Per-project persistent memory in AGD format with selective retrieval and graph traversal. Use when the user says \"ricordi\", \"what do you remember\", \"do you know X\" — or when you need project context (user preferences, paths, past decisions, scope-restricted rules) before answering. The memory file is a graph; pull the TOC first, then fetch the relevant blocks plus their backlinks/refs in a single call. Costs roughly 6-15x fewer tokens than loading the whole memory file (TOC alone ~6x, typical 1-3 block fetch ~14x, single-block ~74x)."
license: MIT
metadata:
  author: pinperepette
  version: "0.4.1"
  domain: tooling
  triggers: memoria, ricordi, ricorda, what do you remember, recall, context, project facts, AGD memory, blog rules, project history
  role: utility
  scope: implementation
---

# AGD Memory

Per-project persistent memory stored as a single AGD document with stable
per-block ids and a `refs="#a,#b"` attribute that links blocks into a
graph. Designed so a session never loads all memory at once: scan the
TOC, decide what is relevant, fetch only those blocks (and optionally
their neighbors).

## Where memory lives

```
~/.claude/projects/<sanitized-cwd>/memory/memory.agd
```

Sanitized-cwd = working directory with `/` replaced by `-`. For
`/Users/alice/work/myproj` the path is
`~/.claude/projects/-Users-alice-work-myproj/memory/memory.agd`.

If the project does not have a memory file, the skill is a silent no-op.

## How to access

Four MCP tools (preferred):

- `agd_memory_toc(kind?)` — list of `{id, kind, desc?}`. Cheap entry
  point; the `desc` attribute (when present) is enough to decide
  whether to pull the body without actually pulling it.
- `agd_memory_search(query, ignore_case?, kind?)` — substring search
  across block bodies. Use when you do not know the exact id.
- `agd_memory_get(ids)` — fetch one or many blocks in a single call.
  Always batch instead of calling get repeatedly.
- `agd_memory_save(kind, id, content)` — add or replace an entry.

CLI fallback (when MCP unavailable):

```sh
agd ids       memory.agd                       # TOC
agd ids       memory.agd --kind x-feedback     # filtered TOC
agd search    memory.agd "topic" -i            # grep on bodies
agd get       memory.agd '#a' '#b' '#c'        # batch fetch
agd backlinks memory.agd '#id'                 # who points to #id
agd get       memory.agd '#id' --with-backlinks   # block + every citer
agd get       memory.agd '#id' --follow-refs --depth 3  # outbound chain
agd edit      memory.agd --op '{...}' -i       # write
```

## Preferred access patterns

The wrong pattern is to call `get` four times in sequence walking a
chain of blocks; that pays four parses for one logical read. The right
patterns are:

**1. Scope query** (rules that apply to topic X):

The convention is that rules with restricted scope carry
`refs="#anchor"`. To pull every rule applying to `#user-blog`:

```sh
agd get memory.agd '#user-blog' --with-backlinks
```

One call, one parse, one round-trip. Returns the anchor block plus
every block that cites it.

**2. Provenance chain** (where does this fact come from):

When a block declares `refs="#source"`, follow the chain transitively:

```sh
agd get memory.agd '#numbers-block' --follow-refs --depth 3
```

Walks the outbound `refs=` graph up to N hops. Cycle-safe via
deduplication.

**3. Topic search** (when you have only a keyword):

```sh
agd search memory.agd "blog" -i
```

Returns id + kind + a short excerpt for each match. Use the ids it
returns as input to a single `agd get` batch.

## When to read vs. when to write

**Read** at the start of a session when you need context about:

- Who the user is (role, preferences, working style) → `kind=x-user`
- How to collaborate with them (rules, conventions) → `kind=x-feedback`
- What the project is currently doing → `kind=x-project`
- Where things are (paths, repos, links) → `kind=x-reference`

**Write** when you learn:

- A non-obvious user preference
- A correction the user gave you
- A new project artifact (commit hash, deployed URL, decision made)
- A reference path you'll need again

Do not save what is already derivable from the codebase, git log, or
the current conversation. Save only what would be lost if the session
ended.

## When to propose `/remember` (soft nudge)

Saves are user-curated. Do not call `agd_memory_save` autonomously.
Instead, when a memory-worthy fact emerges in conversation, suggest
saving it with a single in-line line — never block the flow waiting
for an answer. The `remember` skill handles the actual save once the
user agrees.

**Propose a save when:**

- a stable decision is made (architecture, library choice, naming
  convention, regola di stile)
- a fix is shipped and other sessions will reference it
  ("v0.3.1 fixes X")
- a non-trivial path / env var / command / URL is discovered
- the user states a preference ("always X, never Y")
- a workaround for a known bug is established
- a constraint is discovered (schema enum, system limit, gotcha)

**Do NOT propose** when:

- the fact is visible by `grep`, `Read`, or `git log` — keep memory
  for things not derivable from the code
- the work is in-progress; wait until it's actually decided / shipped
- the fact is already in memory (check the TOC; if close, suggest
  updating the existing block rather than adding a new one)
- the content is sensitive (api keys, tokens, credentials)
- the user is in the middle of a task and a nudge would interrupt

**How to phrase the nudge** (in-line, one line, optional):

> Vuoi che ricordi `#agd-mcp-kind-enum` (gotcha schema MCP save)?

If the user ignores it, drop it — do not nag. If they say yes, save
with the proposed id+kind+desc. If they revise, save the revision.

**Never save without explicit consent.** A "thumbs up", "sì", "ok" or
"fai" is enough; ambiguity is not.

## Entry format

Each entry is one custom block with a fenced body:

```agd
@x-feedback desc="no Co-Authored-By in commits" refs="#user-role" [#feedback-no-coauthored]
~~~
Mai aggiungere Co-Authored-By o trailer nei commit Git.
Reason: regola globale del progetto, perduta se la dimentichi.
~~~
```

The `desc` attribute is used by the TOC to make selective retrieval
self-explanatory. The `refs` attribute is optional; add it when the
block has a meaningful target (the rule applies to a user-fact, the
fact comes from another fact, the reference points to a project).

## Token economy

Measured on a 37-entry memory file (~4.7k tokens total, 2026-05-09):

| pattern                          | tokens | as % of whole |
|----------------------------------|-------:|--------------:|
| whole-doc load                   | 4,743  | 100%          |
| TOC only                         | ~750   | 16% (~6x)     |
| TOC + 1 batch of 3 typical blocks| ~340   | 7% (~14x)     |
| TOC + single-block fetch         | ~64    | 1.4% (~74x)   |

The ratio grows with the corpus: at 100 entries the same pattern saves
about 9x; at 10k entries it saves ~14x on a single-block fetch.
