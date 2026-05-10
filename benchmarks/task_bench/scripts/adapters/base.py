#!/usr/bin/env python3
"""Retrieval adapter interface.

An adapter takes a (repo_root, problem_statement, model, client) and is
responsible for: (a) producing a context for the model, (b) driving any
tool-use loop the strategy requires, (c) returning a model-authored
unified-diff patch.

The harness wraps each adapter run with timing and token accounting.
Adapters are *not* expected to compute RAF or oracle metrics — those
are computed externally from the loaded-files manifest the adapter
returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AdapterResult:
    patch: str                                # unified diff produced by the model
    files_loaded: dict[str, str] = field(default_factory=dict)  # path -> text actually shown to model
    loaded_tokens: int = 0                    # total tokens shown to the model in retrieval+prompt
    tool_calls: int = 0                       # number of retrieval round-trips
    api_input_tokens: int = 0
    api_output_tokens: int = 0
    api_cache_read: int = 0
    api_cache_write: int = 0
    notes: dict[str, Any] = field(default_factory=dict)  # adapter-specific debug info


class RetrievalAdapter:
    name: str = "base"

    def run(
        self,
        repo_root: Path,
        problem_statement: str,
        client: Any,
        model: str,
        token_budget: int,
    ) -> AdapterResult:
        raise NotImplementedError


PATCH_INSTRUCTIONS = """\
You are fixing a real bug in a Python repository.

You will be shown the problem statement and some context from the
repository. Your job: produce a unified diff (`diff --git` format) that
fixes the bug. Touch only the files actually needed.

Output rules:
- Wrap the patch in a single ```diff fenced block. Nothing outside.
- Use `--- a/<path>` / `+++ b/<path>` headers with paths relative to repo root.
- Hunk headers: `@@ -<old_start>,<old_count> +<new_start>,<new_count> @@`.
- No prose commentary. Only the diff.
"""


def extract_patch(text: str) -> str:
    """Pull the unified diff out of a fenced ```diff block. If absent, try
    to recognize a raw `--- a/...` block. If neither, return empty."""
    import re
    m = re.search(r"```diff\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip() + "\n"
    m = re.search(r"(?ms)^(--- a/.*)", text)
    if m:
        return m.group(1).strip() + "\n"
    return ""
