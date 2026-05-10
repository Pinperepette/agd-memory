---
name: remember
description: "Save a fact to the project's agd-memory. Use this skill when the user invokes /remember, says \"ricorda questo\", \"salvalo in memoria\", \"remember this\", \"save to memory\", or asks you to memorise a specific decision/path/version/preference. Two modes: with an argument the text is saved directly; without an argument propose a draft from the recent conversation and wait for the user's confirmation before writing."
license: MIT
metadata:
  author: pinperepette
  version: "0.1.0"
  domain: tooling
  triggers: remember, ricorda, salva in memoria, salvalo, save to memory, memorise, memorize, ricordatelo, save this, remember that, salva questo
  role: utility
  scope: implementation
---

# Save to AGD memory

Explicit channel to add a fact to the per-project memory. The recall
skill is `memory`; this is its write counterpart.

## Two modes

**With an argument** — `/remember <text>` or "ricorda che X":
the user provided the content. Derive `kind`, `id`, `desc` from the
text (rules below), call `agd_memory_save`, confirm with one short
line.

**Without an argument** — `/remember` alone, or "salvalo":
take the last few user/assistant turns as input, draft a single block
`{kind, id, desc, content}`, present it to the user as a preview, wait
for explicit ok before saving. If the user revises (different scope,
shorter content, etc.), update the draft and ask again.

Never save without an explicit confirmation in mode-without-argument.

## Choosing `kind`

The MCP `agd_memory_save` schema restricts `kind` to four values:

| value          | use for                                                |
|----------------|--------------------------------------------------------|
| `x-user`       | identity, preferences, working style of the user       |
| `x-feedback`   | corrections, rules-of-collaboration, decisions, learnings of the session |
| `x-project`    | what *this* project is, scope, current focus           |
| `x-reference`  | reusable technical knowledge: paths, versions, commands, gotchas, schemas |

If ambiguous → default to `x-feedback`. Never invent a custom `x-…`
kind via this skill: the MCP enum will reject it client-side.

## Generating `id`

Stable, kebab-case, semantic. Heuristic:

1. Take the most specific keywords from desc/content (e.g. for "MCP
   save schema restricts kind to enum" → `agd-mcp-kind-enum`).
2. Lowercase, replace spaces with `-`, strip non `[a-z0-9-]`.
3. Cap at ~32 chars.
4. Check the TOC (`agd_memory_toc`) for collisions:
   - same id + same intent → `replace` (update existing entry).
   - same id + different intent → suffix with `-2`, `-2026-05-10`, or
     a more specific qualifier.

Never overwrite silently. If you would replace an entry whose meaning
differs from the new content, surface this to the user first.

## Generating `desc`

One short line, ≤60 chars, present-tense, describes WHY this is in
memory. Examples:

- "no Co-Authored-By in commits"
- "agd v0.3.1 fixes fence-internal corruption"
- "MCP save kind is enum-restricted (gotcha)"

`desc` is what the TOC shows; make it good enough to decide *without*
fetching the body.

## What deserves a save

Save only what would be lost if the session ended *and* would matter
in a future session:

- non-obvious user preferences ("always X, never Y")
- corrections the user gave you
- decisions made (architecture, library, naming)
- versions/SHAs of fixes that other sessions will reference
- paths, env vars, URLs that aren't trivially discoverable
- workarounds for known issues
- constraints discovered (e.g. schema enums, ARG_MAX limits)

Do not save:

- things visible by reading the codebase or `git log`
- ephemeral debug state, in-progress work that will change
- anything sensitive (api keys, tokens, private credentials)
- entries already covered by an existing memory block (check the TOC
  first; offer to update the existing block instead)

When in doubt: ask the user one short clarifying question before
saving.

## After saving

Confirm with a single line: `saved #<id> (<kind>) — <desc>`.
Don't restate the body.

If the body contains a standalone `~~~` line, the save will be
rejected by the plugin (≥ v0.1.1 enforces this — the body would
otherwise corrupt the file). Reword and retry; explain why.
