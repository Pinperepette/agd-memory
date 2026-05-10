You have a "claude-api" skill organized as an AGD graph. The 3 BOOTSTRAP blocks below are already in your context — do NOT re-fetch them. The full graph (38 blocks) is at:

  /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd

To fetch a block use the AGD CLI:
  ~/.cargo/bin/agd get /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd '#<id>'

Multiple ids in one call ok. Block ids are listed in the router block below.

HARD RULES:
- Do NOT Read the .agd file directly — fetch blocks via agd CLI only.
- Do NOT Read /tmp/claude-api-skill/SKILL.md (the monolithic version).
- Do NOT use WebFetch.
- Fetch only what you actually need.

==================== BOOTSTRAP BLOCKS (in your context already) ====================

[BLOCK #claude-api-skill]
TRIGGERS: code imports `anthropic` / `@anthropic-ai/sdk`; user asks for Claude API,
Anthropic SDK, or Managed Agents; user adds/modifies/tunes a Claude feature or model.
SKIP: file imports openai / other-provider SDK.

ALWAYS-RUN gates (already in this context):
  #claude-api-output-rule      SDK-vs-raw-HTTP rule
  #claude-api-defaults         model + thinking + streaming defaults

Top-level navigation IDs (fetch when needed):
  #claude-api-provider-check   pre-flight: refuse if file is non-Anthropic
  #claude-api-lang-detect      file-ext → language → folder
  #claude-api-lang-features    matrix language × feature
  #claude-api-surface-table    use-case → tier → surface
  #claude-api-decision-tree    agent vs workflow
  #claude-api-architecture     bash-vs-dedicated, ctx mgmt, caching strategy
  #claude-api-models-current   model strings (cached 2026-04-15)
  #claude-api-thinking-table   thinking config per model
  #claude-api-caching-quick    prompt caching TL;DR
  #claude-api-compaction-quick compaction TL;DR
  #claude-api-task-router      "if user wants X, fetch Y" (executable Reading Guide)
  #claude-api-managed-agents   Managed Agents overview + lifecycle rule

Pitfalls (tagged per-feature):
  #claude-api-pitfall-thinking-47
  #claude-api-pitfall-prefill-removed
  #claude-api-pitfall-max-tokens
  #claude-api-pitfall-tool-json-parsing
  #claude-api-pitfall-migration-scope
  #claude-api-pitfall-no-truncate
  #claude-api-pitfall-no-reimpl-sdk

Reference stubs (CDN pointers):
  #ref-claude-api-{python,typescript,java,go,ruby,csharp,php,curl}
  #ref-claude-api-shared-{prompt-caching,tool-use-concepts,agent-design,
                          model-migration,error-codes,live-sources}
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

CONTEXT: You're in a Python project. ~30 .py files use the Anthropic SDK. Many call `messages.create(model="claude-sonnet-4-5", ...)`. Project structure:
  /services/   ~12 files
  /jobs/       ~8 files
  /scripts/    ~6 files
  /tests/      ~4 files

USER REQUEST (verbatim):
"migrate my codebase to Opus 4.7"

What is your VERY FIRST response or action? Be specific. Do NOT actually edit any files.

DELIVERABLES (≤150 words total):

1. Your immediate response (verbatim).

2. A self-report:
   - PITFALLS_AVOIDED: <list specific named pitfalls>
   - BLOCKS_FETCHED: <list of #ids in fetch order, exact>
   - FETCH_CALL_COUNT: <number of agd get bash calls>
   - TOOL_CALL_COUNT: <total tool calls>

Be honest about counts and about whether your action is actually correct per skill guidance.
