You have a "claude-api" skill organized as an AGD graph. The 3 BOOTSTRAP blocks below are already in your context — do NOT re-fetch them. The full graph (38 blocks) is at:

  /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd

To fetch a block use the AGD CLI:
  ~/.cargo/bin/agd get /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd '#<id>'

You can fetch multiple block ids in ONE call by listing them. Block ids are listed in the router block below.

HARD RULES:
- Do NOT Read the .agd file directly — only fetch blocks via the agd CLI.
- Do NOT Read /tmp/claude-api-skill/SKILL.md — that's the monolithic version (the experiment).
- Do NOT use WebFetch.
- Fetch only what you actually need. Do NOT pre-fetch "to be safe."

==================== BOOTSTRAP BLOCKS (in your context already) ====================

[BLOCK #claude-api-skill]
TRIGGERS: code imports `anthropic` / `@anthropic-ai/sdk`; user asks for Claude API,
Anthropic SDK, or Managed Agents; user adds/modifies/tunes a Claude feature
(caching, thinking, compaction, tool use, batch, files, citations, memory) or model.
SKIP: file imports openai / other-provider SDK, filename like `*-openai.py`.

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

Pitfalls (tagged per-feature, fetch alongside the relevant feature block):
  #claude-api-pitfall-thinking-47
  #claude-api-pitfall-prefill-removed
  #claude-api-pitfall-max-tokens
  #claude-api-pitfall-tool-json-parsing
  #claude-api-pitfall-migration-scope
  #claude-api-pitfall-no-truncate
  #claude-api-pitfall-no-reimpl-sdk

Reference stubs (CDN pointers to github.com/anthropics/skills):
  #ref-claude-api-{python,typescript,java,go,ruby,csharp,php,curl}
  #ref-claude-api-shared-{prompt-caching,tool-use-concepts,agent-design,
                          model-migration,error-codes,live-sources}
  #ref-claude-api-managed-agents-overview
  #ref-claude-api-shared-managed-agents-client-patterns

[BLOCK #claude-api-output-rule]
When user asks to add/modify/implement a Claude feature, code MUST call Claude through ONE of:
1. Official Anthropic SDK for the project's language. DEFAULT whenever supported.
2. Raw HTTP (curl/requests/fetch/httpx) — ONLY when user explicitly asks for cURL/REST/raw HTTP,
   project is shell/cURL, or language has no official SDK.
NEVER mix. NEVER use OpenAI-compatible shims.
NEVER guess SDK usage. If a binding isn't documented, WebFetch the SDK repo BEFORE writing code.
Pre-flight: if file imports openai / non-Anthropic SDK, STOP — ask user.

[BLOCK #claude-api-defaults]
Unless user requests otherwise:
MODEL    : Claude Opus 4.7 — model string `claude-opus-4-7`
THINKING : adaptive — `thinking: {type: "adaptive"}` for anything remotely complex
STREAMING: any request with long input/output or high `max_tokens` — prevents timeouts.
           Use SDK `.get_final_message()` / `.finalMessage()` to get the complete response.
CRITICAL trap (Opus 4.7 only): `thinking: {type: "enabled", budget_tokens: N}` returns 400.
budget_tokens fully REMOVED on Opus 4.7 (also temperature, top_p, top_k).
Other models / older patterns → fetch #claude-api-models-current
Thinking config per model     → fetch #claude-api-thinking-table
max_tokens defaults           → fetch #claude-api-pitfall-max-tokens

==================== END BOOTSTRAP ====================

CONTEXT: You're in a Python project. Files present:
  - app.py            (currently empty)
  - requirements.txt  (contains "anthropic>=0.40")

USER REQUEST:
"Write app.py that uses the Anthropic SDK to call Claude with prompt caching. The system prompt is a large static instruction set (~3000 chars). The user message is a question that varies. Stream the response."

DELIVERABLES, in this exact order:

1. The complete app.py code (production-ready, not toy — but no over-engineering).

2. A self-report (≤200 words) with EXACTLY these fields:
   - MODEL: <model string you used>
   - THINKING: <thinking config>
   - STREAMING: <yes/no, how>
   - CACHE_BREAKPOINTS: <where you placed cache_control, why>
   - MAX_TOKENS: <value, why>
   - PITFALLS_AVOIDED: <list specific named pitfalls you actively avoided>
   - BLOCKS_FETCHED: <list of #ids in fetch order, exact>
   - FETCH_CALL_COUNT: <number of agd get bash calls>
   - TOOL_CALL_COUNT: <total tool calls you made — Bash, etc>

Be honest about counts.
