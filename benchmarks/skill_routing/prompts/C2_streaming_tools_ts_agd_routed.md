You have a "claude-api" skill organized as an AGD graph. The 3 BOOTSTRAP blocks below are already in your context — do NOT re-fetch them. Full graph (38 blocks) at:

  /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd

Fetch via:
  ~/.cargo/bin/agd get /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd '#<id>' '#<id2>' ...

HARD RULES:
- Do NOT Read the .agd file directly — fetch blocks via agd CLI only.
- Do NOT Read /tmp/claude-api-skill/SKILL.md (the monolithic version).
- Do NOT use WebFetch.
- Fetch only what you actually need.

==================== BOOTSTRAP BLOCKS ====================

[BLOCK #claude-api-skill]
TRIGGERS: code imports anthropic / @anthropic-ai/sdk; user asks for Claude API,
Anthropic SDK, or Managed Agents; user adds/modifies/tunes a Claude feature.
SKIP: file imports openai / other-provider SDK.

ALWAYS-RUN gates (already in context):
  #claude-api-output-rule      SDK-vs-raw-HTTP rule
  #claude-api-defaults         model + thinking + streaming defaults

Top-level navigation IDs (fetch when needed):
  #claude-api-provider-check, #claude-api-lang-detect, #claude-api-lang-features,
  #claude-api-surface-table, #claude-api-decision-tree, #claude-api-architecture,
  #claude-api-models-current, #claude-api-thinking-table,
  #claude-api-caching-quick, #claude-api-compaction-quick,
  #claude-api-task-router, #claude-api-managed-agents

Pitfalls (tagged per-feature):
  #claude-api-pitfall-{thinking-47, prefill-removed, max-tokens,
                       tool-json-parsing, migration-scope, no-truncate, no-reimpl-sdk}

Reference stubs (CDN pointers to github.com/anthropics/skills):
  #ref-claude-api-{python, typescript, java, go, ruby, csharp, php, curl}
  #ref-claude-api-shared-{prompt-caching, tool-use-concepts, agent-design,
                          model-migration, error-codes, live-sources}
  #ref-claude-api-managed-agents-overview
  #ref-claude-api-shared-managed-agents-client-patterns

[BLOCK #claude-api-output-rule]
SDK by default; raw HTTP only on explicit request / no SDK. NEVER mix. NEVER OpenAI shims.
NEVER guess SDK usage — WebFetch repo if undocumented. Pre-flight refuse on non-Anthropic file.

[BLOCK #claude-api-defaults]
MODEL: claude-opus-4-7 · THINKING: adaptive · STREAMING: long-input/output
CRITICAL: Opus 4.7 budget_tokens REMOVED (returns 400). Use adaptive.
Other models → #claude-api-models-current ; Thinking per model → #claude-api-thinking-table

==================== END BOOTSTRAP ====================

CONTEXT: TypeScript project at /tmp/agd-bench/c2-agd-routed/. Files present:
  - app.ts          (currently empty)
  - package.json    (contains "@anthropic-ai/sdk": "^0.40.0")
  - tsconfig.json   (target ES2022, module esnext)

USER REQUEST:
"Add a tool-use loop to app.ts. The model should be able to call a tool `get_weather(city: string)` (mock the implementation — return `{ temp: 22, conditions: "sunny" }`), then incorporate the result and stream the final response to stdout."

NAVIGATE THE GRAPH: fetch only blocks you actually need.

DELIVERABLES:

1. Write the complete app.ts.

2. A self-report (≤200 words) with EXACTLY these fields:
   - MODEL: <model string>
   - THINKING: <thinking config>
   - STREAMING: <yes/no, how>
   - TOOL_LOOP_PATTERN: <SDK helper name OR manual loop, why>
   - PITFALLS_AVOIDED: <list>
   - BLOCKS_FETCHED: <list of #ids in fetch order>
   - FETCH_CALL_COUNT: <number of agd get bash calls>
   - TOOL_CALL_COUNT: <total tool calls>

Be honest about counts.
