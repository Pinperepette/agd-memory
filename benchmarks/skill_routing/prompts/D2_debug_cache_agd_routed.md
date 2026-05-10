You have a "claude-api" skill organized as an AGD graph. The 3 BOOTSTRAP blocks below are already in your context — do NOT re-fetch them. Full graph (38 blocks) at:

  /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd

Fetch via:
  ~/.cargo/bin/agd get /Users/pinperepette/Desktop/test-laboratorio/test/skill-claude-api.agd '#<id>' '#<id2>' ...

HARD RULES:
- Do NOT Read the .agd file directly — fetch via agd CLI only.
- Do NOT Read /tmp/claude-api-skill/SKILL.md.
- Do NOT use WebFetch.
- Fetch only what you actually need.

==================== BOOTSTRAP BLOCKS ====================

[BLOCK #claude-api-skill]
TRIGGERS: code imports anthropic / @anthropic-ai/sdk; user asks for Claude API,
Anthropic SDK, or Managed Agents; user adds/modifies/tunes a Claude feature.
SKIP: file imports openai / other-provider SDK.

ALWAYS-RUN gates (already in context):
  #claude-api-output-rule, #claude-api-defaults

Top-level navigation IDs:
  #claude-api-provider-check, #claude-api-lang-detect, #claude-api-lang-features,
  #claude-api-surface-table, #claude-api-decision-tree, #claude-api-architecture,
  #claude-api-models-current, #claude-api-thinking-table,
  #claude-api-caching-quick, #claude-api-compaction-quick,
  #claude-api-task-router, #claude-api-managed-agents

Pitfalls (tagged per-feature):
  #claude-api-pitfall-{thinking-47, prefill-removed, max-tokens,
                       tool-json-parsing, migration-scope, no-truncate, no-reimpl-sdk}

Reference stubs (CDN pointers):
  #ref-claude-api-{python, typescript, java, go, ruby, csharp, php, curl}
  #ref-claude-api-shared-{prompt-caching, tool-use-concepts, agent-design,
                          model-migration, error-codes, live-sources}
  #ref-claude-api-managed-agents-overview
  #ref-claude-api-shared-managed-agents-client-patterns

[BLOCK #claude-api-output-rule]
SDK by default; raw HTTP only on explicit request / no SDK. NEVER mix. NEVER OpenAI shims.
NEVER guess SDK usage. Pre-flight refuse on non-Anthropic file.

[BLOCK #claude-api-defaults]
MODEL: claude-opus-4-7 · THINKING: adaptive · STREAMING: long-input/output
CRITICAL: Opus 4.7 budget_tokens REMOVED (returns 400). Use adaptive.

==================== END BOOTSTRAP ====================

CONTEXT: User shows you their Python file `chat.py` (paste below) and says:
"I added cache_control to my system prompt but cache_read_input_tokens is always 0. The static instructions are 4000 chars long. Why isn't it caching?"

```python
# chat.py
from anthropic import Anthropic
from datetime import datetime
import uuid

client = Anthropic()

STATIC_INSTRUCTIONS = open("instructions.md").read()  # 4000 chars

def chat(user_msg: str) -> str:
    request_id = str(uuid.uuid4())
    system_prompt = f"""{STATIC_INSTRUCTIONS}

Request ID: {request_id}
Current time: {datetime.now().isoformat()}
"""
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"}
        }],
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text
```

NAVIGATE THE GRAPH: fetch only blocks you actually need.

DELIVERABLES (≤200 words total):

1. Your diagnosis: what specifically is wrong? Be precise.
2. The fix: corrected snippet.
3. Self-report:
   - ROOT_CAUSE: <one sentence>
   - PITFALLS_AVOIDED_OR_REFERENCED: <list>
   - BLOCKS_FETCHED: <list of #ids in fetch order>
   - FETCH_CALL_COUNT: <number of agd get bash calls>
   - TOOL_CALL_COUNT: <total tool calls>

Be honest about counts.
