# Subagent prompts (verbatim)

The exact prompts dispatched to Claude Code's `Agent` tool for each cell of
the §S8 benchmark.

| cell | task                       | arm        | file                                     |
| ---- | -------------------------- | ---------- | ---------------------------------------- |
| A1   | Python — caching           | monolithic | `A1_caching_monolithic.md`               |
| A2   | Python — caching           | AGD routed | `A2_caching_agd_routed.md`               |
| B1   | migration scope            | monolithic | `B1_migration_monolithic.md`             |
| B2   | migration scope            | AGD routed | `B2_migration_agd_routed.md`             |
| C1   | TS — tool loop + stream    | monolithic | `C1_streaming_tools_ts_monolithic.md`    |
| C2   | TS — tool loop + stream    | AGD routed | `C2_streaming_tools_ts_agd_routed.md`    |
| D1   | debug cache hit            | monolithic | `D1_debug_cache_monolithic.md`           |
| D2   | debug cache hit            | AGD routed | `D2_debug_cache_agd_routed.md`           |

The monolithic and AGD prompts for each task share the task wording verbatim;
they differ only in the skill-access block at the top.

To re-run, paste the contents of any file as the `prompt` argument of an
`Agent({subagent_type: "general-purpose", ...})` call. Cells run independently
and do not share state.

**Path assumptions** (rewrite if your environment differs):

- `/tmp/claude-api-skill/SKILL.md` — the monolithic skill snapshot
  (corresponds to `../corpus/claude-api-SKILL.md`).
- `/Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd` —
  the AGD sidecar (corresponds to `../corpus/skill-claude-api.agd`).
- `~/.cargo/bin/agd` — the AGD CLI (≥ v0.3.0).
- `/tmp/agd-bench/c{1,2}-*/` — separate working dirs for tasks C1 and C2 to
  avoid file collisions on the produced `app.ts`.
