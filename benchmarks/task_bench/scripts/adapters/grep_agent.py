#!/usr/bin/env python3
"""Grep-agent adapter: multi-turn tool-use loop with ripgrep + read_file.

The realistic baseline for "agent without addressable memory". The
model is given three tools that mirror what a developer running
`vim` does: search by regex, list a directory, read a slice of a
file. It iterates until it produces a final unified diff.

Why this is the *honest* baseline:
- Many production coding agents (Aider, OpenHands, Devin-style)
  reduce to this pattern.
- It already wins handsomely against whole-preload at large repo
  sizes — comparing against this is the steel-man, not the strawman.

Token accounting:
- `loaded_tokens` = total tokens of *new* content shown to the model
  across all turns, plus the system prompt and problem statement.
  We don't double-count cached prefixes (they aren't paid for in
  context anyway from the RAF perspective: the question is "how much
  text did the model have to read?").
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

import tiktoken

from .base import RetrievalAdapter, AdapterResult, PATCH_INSTRUCTIONS, extract_patch

ENC = tiktoken.get_encoding("cl100k_base")
MAX_TURNS = 25
TOOL_OUTPUT_CAP = 4000  # chars; rg/cat outputs get truncated to this


SYSTEM = """You are a senior Python engineer fixing a real bug. You have four tools.

Navigation:
- ripgrep_search: regex search (uses `rg`); returns file:line:text matches
- read_file: read a slice of a file (path, start_line, end_line)
- list_dir: list contents of a directory

Submission (MANDATORY):
- submit_patch: call this with the unified diff when ready. The diff MUST start with `--- a/<path>` and use proper @@ hunks. Do NOT emit the diff as text — only via this tool.

Strategy:
1. ripgrep on the symbol or string mentioned in the problem.
2. Read just the function bodies you need. Stop reading once you understand the change.
3. Call submit_patch with the final diff. Exactly once.

Be efficient: typical fix is 1-3 ripgrep calls + 1-3 read_file calls + submit_patch. Do not over-explore."""


TOOLS = [
    {
        "name": "ripgrep_search",
        "description": "Search the repository with ripgrep. Returns file:line:text matches.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "Optional sub-path; defaults to repo root", "default": ""},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a slice of a file. Lines are 1-indexed, inclusive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer", "default": 200},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List entries of a directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": ""}},
        },
    },
    {
        "name": "submit_patch",
        "description": "Submit the final unified diff. Call this exactly once when the fix is ready. Use `--- a/<path>` / `+++ b/<path>` headers and proper @@ hunks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "The complete unified diff."},
            },
            "required": ["diff"],
        },
    },
]


def _safe_path(repo: Path, sub: str) -> Path:
    sub = (sub or "").lstrip("/")
    p = (repo / sub).resolve()
    if not str(p).startswith(str(repo.resolve())):
        raise ValueError(f"path escapes repo root: {sub}")
    return p


def _tool_ripgrep(repo: Path, pattern: str, path: str = "", max_results: int = 50) -> str:
    target = _safe_path(repo, path) if path else repo
    cmd = ["rg", "--no-heading", "--line-number", "--color=never",
           "-S", "--max-count", str(max_results), "--max-columns", "200",
           "-g", "!.git", "-g", "!__pycache__", "-g", "!*.pyc",
           pattern, str(target)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "[ripgrep timeout]"
    text = out.stdout if out.returncode in (0, 1) else (out.stderr or "[no output]")
    if len(text) > TOOL_OUTPUT_CAP:
        text = text[:TOOL_OUTPUT_CAP] + f"\n[…truncated, {len(text)-TOOL_OUTPUT_CAP} chars dropped]"
    if out.returncode == 1 and not text.strip():
        return "[no matches]"
    # Strip absolute repo prefix to keep paths relative
    text = text.replace(str(repo.resolve()) + "/", "")
    return text


def _tool_read(repo: Path, path: str, start_line: int = 1, end_line: int = 200) -> str:
    p = _safe_path(repo, path)
    if not p.is_file():
        return f"[not a file: {path}]"
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError as e:
        return f"[read error: {e}]"
    s = max(1, start_line)
    e = min(len(lines), end_line)
    if s > len(lines):
        return f"[file has only {len(lines)} lines]"
    out = []
    for i in range(s, e + 1):
        out.append(f"{i:6d}\t{lines[i-1]}")
    text = "\n".join(out)
    if len(text) > TOOL_OUTPUT_CAP:
        text = text[:TOOL_OUTPUT_CAP] + f"\n[…truncated]"
    return text


def _tool_list(repo: Path, path: str = "") -> str:
    p = _safe_path(repo, path) if path else repo
    if not p.is_dir():
        return f"[not a directory: {path}]"
    items = []
    for child in sorted(p.iterdir()):
        if child.name in {".git", "__pycache__", ".tox", "node_modules"}:
            continue
        marker = "/" if child.is_dir() else ""
        items.append(f"{child.name}{marker}")
    return "\n".join(items) if items else "[empty]"


def _dispatch(repo: Path, name: str, args: dict[str, Any]) -> str:
    if name == "ripgrep_search":
        return _tool_ripgrep(repo, args.get("pattern", ""), args.get("path", ""), args.get("max_results", 50))
    if name == "read_file":
        return _tool_read(repo, args["path"], args.get("start_line", 1), args.get("end_line", 200))
    if name == "list_dir":
        return _tool_list(repo, args.get("path", ""))
    return f"[unknown tool: {name}]"


class GrepAgent(RetrievalAdapter):
    name = "grep-agent"

    def run(
        self,
        repo_root: Path,
        problem_statement: str,
        client,
        model: str,
        token_budget: int,
    ) -> AdapterResult:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [
                {"type": "text", "text": f"# Problem\n{problem_statement}\n\n{PATCH_INSTRUCTIONS}"}
            ]}
        ]
        loaded_tokens = len(ENC.encode(SYSTEM)) + len(ENC.encode(problem_statement)) + len(ENC.encode(PATCH_INSTRUCTIONS))
        files_loaded: dict[str, str] = {}
        tool_calls = 0
        api_in = api_out = api_cw = api_cr = 0

        for turn in range(MAX_TURNS):
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            )
            api_in += resp.usage.input_tokens
            api_out += resp.usage.output_tokens
            api_cr += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            api_cw += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

            assistant_blocks = list(resp.content)
            messages.append({"role": "assistant", "content": [b.model_dump() for b in assistant_blocks]})

            tool_uses = [b for b in assistant_blocks if b.type == "tool_use"]
            text_blocks = [b for b in assistant_blocks if b.type == "text"]
            text_so_far = "\n".join(b.text for b in text_blocks)

            # Check for submit_patch — this is the canonical termination
            for tu in tool_uses:
                if tu.name == "submit_patch":
                    diff = (tu.input or {}).get("diff", "")
                    patch = extract_patch(diff) or (diff.strip() + "\n" if diff.strip() else "")
                    return AdapterResult(
                        patch=patch,
                        files_loaded=files_loaded,
                        loaded_tokens=loaded_tokens,
                        tool_calls=tool_calls + 1,
                        api_input_tokens=api_in,
                        api_output_tokens=api_out,
                        api_cache_read=api_cr,
                        api_cache_write=api_cw,
                        notes={"turns": turn + 1, "stop_reason": "submit_patch"},
                    )

            if not tool_uses:
                # Fallback: model emitted final answer as text
                patch = extract_patch(text_so_far)
                return AdapterResult(
                    patch=patch,
                    files_loaded=files_loaded,
                    loaded_tokens=loaded_tokens,
                    tool_calls=tool_calls,
                    api_input_tokens=api_in,
                    api_output_tokens=api_out,
                    api_cache_read=api_cr,
                    api_cache_write=api_cw,
                    notes={"turns": turn + 1, "stop_reason": "no_tool_use"},
                )

            tool_results = []
            for tu in tool_uses:
                tool_calls += 1
                try:
                    out = _dispatch(repo_root, tu.name, tu.input or {})
                except Exception as e:
                    out = f"[tool error: {type(e).__name__}: {e}]"
                loaded_tokens += len(ENC.encode(out))
                # Track file content loaded (best-effort; from read_file only)
                if tu.name == "read_file":
                    p = (tu.input or {}).get("path", "")
                    if p and "[not a file" not in out and "[file has only" not in out:
                        files_loaded[p] = files_loaded.get(p, "") + "\n" + out
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": out,
                })
            messages.append({"role": "user", "content": tool_results})

        # Hit MAX_TURNS without a final patch
        return AdapterResult(
            patch="",
            files_loaded=files_loaded,
            loaded_tokens=loaded_tokens,
            tool_calls=tool_calls,
            api_input_tokens=api_in,
            api_output_tokens=api_out,
            api_cache_read=api_cr,
            api_cache_write=api_cw,
            notes={"turns": MAX_TURNS, "stop_reason": "max_turns"},
        )
