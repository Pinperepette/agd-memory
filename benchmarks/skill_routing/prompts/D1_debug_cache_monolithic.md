You have access to a "claude-api" skill at: /tmp/claude-api-skill/SKILL.md

START by reading that file in full. Do NOT use WebFetch. Do NOT read any .agd files.

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

DELIVERABLES (≤200 words total):

1. Your diagnosis: what specifically is wrong? Be precise.
2. The fix: show the corrected snippet.
3. A self-report with EXACTLY these fields:
   - ROOT_CAUSE: <one sentence>
   - PITFALLS_AVOIDED_OR_REFERENCED: <list specific named pitfalls>
   - SKILL_SECTIONS_USED: <header names>
   - TOOL_CALL_COUNT: <total tool calls>

Be honest about counts.
