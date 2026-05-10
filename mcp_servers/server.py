#!/usr/bin/env python3.12
"""
MCP server: per-project AGD memory exposed as three tools.

Tools:
  - agd_memory_toc(kind?)        list memory entries
  - agd_memory_get(id)           fetch one block
  - agd_memory_save(kind, id, content)
                                 add or update an entry

The memory file is derived from the working directory at startup:
  ~/.claude/projects/<sanitized-cwd>/memory/memory.agd
where sanitized-cwd is cwd with `/` replaced by `-`. Override via the
AGD_MEMORY_FILE env var if you want a different path.

The `agd` CLI binary is located via $AGD_BIN, defaulting to
~/.cargo/bin/agd.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


AGD_BIN = Path(os.environ.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))


def memory_path() -> Path:
    override = os.environ.get("AGD_MEMORY_FILE")
    if override:
        return Path(override)
    cwd = Path(os.environ.get("AGD_MEMORY_PROJECT_CWD", os.getcwd())).resolve()
    sanitized = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / "memory" / "memory.agd"


def _run(args: list[str], stdin: str | None = None) -> str:
    return subprocess.run(
        args, input=stdin, text=True, check=True, capture_output=True
    ).stdout


def _ensure_memory_file() -> Path | None:
    p = memory_path()
    return p if p.is_file() else None


_SCAFFOLD = """\
@meta format=agd version=1 [#meta]

@h1 Memoria di sessione [#root]

@h2 User [#h-user]

@h2 Feedback [#h-feedback]

@h2 Project [#h-project]

@h2 Reference [#h-reference]
"""


def _bootstrap_memory_file() -> Path:
    p = memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_SCAFFOLD)
    return p


server = Server("agd-memory")


@server.list_tools()
async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="agd_memory_toc",
            description=(
                "List the addressable block ids in the current project's "
                "AGD memory. This is the cheap entry-point: call it first, "
                "decide which block(s) you actually need, then call "
                "agd_memory_get(). Optional `kind` filter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": (
                            "Filter by block kind. Standard kinds: x-user, "
                            "x-feedback, x-project, x-reference."
                        ),
                        "enum": ["x-user", "x-feedback", "x-project", "x-reference"],
                    },
                },
            },
        ),
        Tool(
            name="agd_memory_get",
            description=(
                "Fetch one or more memory blocks by id in a single call. "
                "Pass multiple ids to amortise the parse cost across all of "
                "them — the file is parsed once. Each block is typically "
                "30-200 tokens. Pass either `id` (single) or `ids` (list)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Single block id, with or without leading '#'.",
                    },
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple block ids in one call.",
                    },
                },
            },
        ),
        Tool(
            name="agd_memory_search",
            description=(
                "Substring search across all memory block bodies. Returns "
                "matching ids with a short excerpt around each match. Use "
                "this when you don't know the exact id — e.g. 'where did I "
                "save preferences about commit messages?'. Cheap: parses "
                "once, scans bodies, returns id + ~80-char excerpt per hit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to search for in block bodies.",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case-insensitive match. Default true.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["x-user", "x-feedback", "x-project", "x-reference"],
                        "description": "Restrict search to blocks of this kind.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="agd_memory_save",
            description=(
                "Add a new memory entry, or replace an existing one with the "
                "same id. The body is stored verbatim inside a fence so it "
                "round-trips losslessly."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["x-user", "x-feedback", "x-project", "x-reference"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Stable id, kebab-case. e.g. 'feedback-no-coauthored'.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Body of the memory entry, plain text.",
                    },
                    "desc": {
                        "type": "string",
                        "description": (
                            "Optional one-line description shown in the TOC. "
                            "Whenever the body changes substantially, update "
                            "the desc too — keeps the TOC honest."
                        ),
                    },
                },
                "required": ["kind", "id", "content"],
            },
        ),
    ]


@server.call_tool()
async def _call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    arguments = arguments or {}
    mem = _ensure_memory_file()
    if mem is None:
        if name == "agd_memory_save":
            mem = _bootstrap_memory_file()
        else:
            return [TextContent(
                type="text",
                text=(
                    f"No AGD memory file for this project at {memory_path()}. "
                    "Save a first entry via agd_memory_save to bootstrap one."
                ),
            )]

    if name == "agd_memory_toc":
        kind = arguments.get("kind")
        cmd = [str(AGD_BIN), "ids", str(mem), "--json"]
        if kind:
            cmd += ["--kind", kind]
        out = _run(cmd)
        return [TextContent(type="text", text=out.strip())]

    if name == "agd_memory_get":
        # Accept either `id` (single, legacy) or `ids` (list).
        ids: list[str] = []
        if "ids" in arguments and arguments["ids"]:
            ids = list(arguments["ids"])
        elif "id" in arguments and arguments["id"]:
            ids = [arguments["id"]]
        else:
            return [TextContent(type="text", text="provide either 'id' or 'ids'")]
        cli_ids = [f"#{x.lstrip('#')}" for x in ids]
        try:
            out = _run([str(AGD_BIN), "get", str(mem), *cli_ids])
            return [TextContent(type="text", text=out)]
        except subprocess.CalledProcessError as e:
            return [TextContent(
                type="text",
                text=f"get failed: {e.stderr.strip()}",
            )]

    if name == "agd_memory_search":
        query = arguments["query"]
        ignore_case = arguments.get("ignore_case", True)
        kind = arguments.get("kind")
        cmd = [str(AGD_BIN), "search", str(mem), query, "--json"]
        if ignore_case:
            cmd.append("-i")
        if kind:
            cmd += ["--kind", kind]
        try:
            out = _run(cmd)
            return [TextContent(type="text", text=out.strip() or "[]")]
        except subprocess.CalledProcessError as e:
            return [TextContent(
                type="text",
                text=f"search failed: {e.stderr.strip()}",
            )]

    if name == "agd_memory_save":
        kind = arguments["kind"]
        block_id = arguments["id"].lstrip("#")
        content = arguments["content"]

        # Build attrs: include desc only if explicitly given. When updating
        # an existing entry without a new desc, preserve the previous one
        # by reading it back via JSON parse.
        desc = arguments.get("desc")
        attrs = {}
        if desc:
            attrs["desc"] = desc

        existing_ids_raw = _run([str(AGD_BIN), "ids", str(mem)]).strip().splitlines()
        # ids output is "id\tdesc" or just "id" — split to get just the id
        existing_ids = [line.split("\t", 1)[0] for line in existing_ids_raw]

        # If we're replacing and no new desc was passed, preserve the existing one.
        if block_id in existing_ids and not desc:
            try:
                existing_json = _run([str(AGD_BIN), "get", str(mem), f"#{block_id}", "--json"])
                existing_block = json.loads(existing_json)
                if isinstance(existing_block, list) and existing_block:
                    existing_block = existing_block[0]
                old_desc = (existing_block.get("attrs") or {}).get("desc")
                if old_desc:
                    attrs["desc"] = old_desc
            except Exception:
                pass

        block = {
            "kind": kind,
            "attrs": attrs,
            "id": block_id,
            "content": {"type": "fenced", "value": content},
        }

        if block_id in existing_ids:
            op = {"op": "replace", "id": block_id, "with": block}
            verb = "updated"
        else:
            kind_short = kind.removeprefix("x-")
            heading_id = f"h-{kind_short}"
            if heading_id in existing_ids:
                op = {"op": "insert_after", "id": heading_id, "block": block}
            else:
                # Fallback: append after root
                op = {"op": "insert_after", "id": "root", "block": block}
            verb = "added"

        try:
            _run([str(AGD_BIN), "edit", str(mem), "--op", json.dumps(op), "-i"])
            return [TextContent(
                type="text",
                text=f"{verb} #{block_id} ({kind}) in {mem.name}",
            )]
        except subprocess.CalledProcessError as e:
            return [TextContent(
                type="text",
                text=f"save failed: {e.stderr.strip()}",
            )]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
