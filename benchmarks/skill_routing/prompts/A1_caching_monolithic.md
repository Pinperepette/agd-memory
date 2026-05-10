You have access to a "claude-api" skill at: /tmp/claude-api-skill/SKILL.md

START by reading that file in full with the Read tool. This simulates monolithic preload — the entire skill is loaded into your context upfront. Do NOT use WebFetch. Do NOT read any .agd files. Do NOT search for other skill content.

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
   - SKILL_SECTIONS_USED: <header names you actually consulted from the SKILL.md>
   - TOOL_CALL_COUNT: <total tool calls you made — Read, Bash, etc>

Be honest about TOOL_CALL_COUNT and SKILL_SECTIONS_USED — count them, don't estimate.
