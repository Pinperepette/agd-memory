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
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


AGD_BIN = Path(os.environ.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from agd_memory_paths import global_memory_file, project_memory_file  # noqa: E402
from agd_memory_write import (  # noqa: E402
    SaveRejected,
    VALID_STATUS,
    bootstrap_memory_file,
    reject_invalid_block,
    reject_unfencable,
    save_block,
    toc_entries,
)

# Kept under their old private names: the tests and the historical call
# sites address them this way, and the guard rails they encode are the
# reason this module exists.
_reject_invalid_block = reject_invalid_block
_reject_unfencable = reject_unfencable
_VALID_STATUS = VALID_STATUS


def memory_path(scope: str = "project") -> Path:
    if scope == "global":
        return global_memory_file()
    return project_memory_file()


def _run(args: list[str], stdin: str | None = None) -> str:
    return subprocess.run(
        args, input=stdin, text=True, check=True, capture_output=True
    ).stdout


def _ensure_memory_file(scope: str = "project") -> Path | None:
    p = memory_path(scope)
    return p if p.is_file() else None


def _memory_files(scope: str) -> list[Path]:
    """Existing memory files for a read, project layer first.

    Reads default to both layers: a standing user preference is as much
    an answer to "what do you know" as a project fact, and keeping them
    invisible from inside a project is what forced them to be re-learned
    per repo in the first place.
    """
    scopes = ("project", "global") if scope == "all" else (scope,)
    return [p for s in scopes if (p := _ensure_memory_file(s)) is not None]


def _bootstrap_memory_file(scope: str = "project") -> Path:
    return bootstrap_memory_file(memory_path(scope))


def _toc_entries(mem: Path, **kw) -> list[dict]:
    return toc_entries(AGD_BIN, mem, **kw)


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
                    "status": {
                        "type": "string",
                        "enum": list(_VALID_STATUS),
                        "description": (
                            "Filter by lifecycle state — e.g. `open` to list "
                            "what is still pending."
                        ),
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "ISO date (YYYY-MM-DD). Keep only entries updated "
                            "on or after it — answers 'what changed this week'."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["all", "project", "global"],
                        "description": (
                            "Which layer to read. `all` (default) covers both "
                            "the project's memory and the global one holding "
                            "standing user preferences; `project` or `global` "
                            "restrict it."
                        ),
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
                "30-200 tokens. Pass either `id` (single) or `ids` (list). "
                "Set `with_backlinks` to also pull every block that points to "
                "the requested ids (the scoped rule/anchor that cites this "
                "block), or `follow_refs` (with `depth`) to walk outbound "
                "`refs=`/`@ref` links — the graph traversal the memory file is "
                "built for, in one round-trip."
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
                    "with_backlinks": {
                        "type": "boolean",
                        "description": (
                            "Also include every block that references the "
                            "requested ids (inbound edges). Default false."
                        ),
                    },
                    "follow_refs": {
                        "type": "boolean",
                        "description": (
                            "Follow outbound `refs=`/inline `@ref` links up to "
                            "`depth` hops. Cycle-safe. Default false."
                        ),
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Max hops for follow_refs (default 1).",
                        "minimum": 1,
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["all", "project", "global"],
                        "description": (
                            "Which layer to read. `all` (default) covers both "
                            "the project's memory and the global one holding "
                            "standing user preferences; `project` or `global` "
                            "restrict it."
                        ),
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
                    "scope": {
                        "type": "string",
                        "enum": ["all", "project", "global"],
                        "description": (
                            "Which layer to read. `all` (default) covers both "
                            "the project's memory and the global one holding "
                            "standing user preferences; `project` or `global` "
                            "restrict it."
                        ),
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
                "round-trips losslessly. Any `#other-block-id` you mention in "
                "the body is automatically recorded as a graph edge, so "
                "writing 'builds on #earlier-decision' in prose is enough to "
                "make it traversable by agd_memory_get(follow_refs=true)."
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
                    "refs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional explicit outbound edges to other block "
                            "ids. Usually unnecessary: ids cited in the body "
                            "are picked up automatically. Ids that don't "
                            "exist in the file are dropped."
                        ),
                    },
                    "status": {
                        "type": "string",
                        "enum": list(_VALID_STATUS),
                        "description": (
                            "Lifecycle state. Use `done` for work that "
                            "finished and `open` for something still "
                            "pending, instead of writing 'FATTO'/'TODO' into "
                            "the desc. `created`/`updated` dates are stamped "
                            "automatically — don't put them in the text."
                        ),
                    },
                    "supersedes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ids this entry replaces. Each one is flagged "
                            "`status=superseded` so stale facts stop being "
                            "recalled as if current. Use this instead of "
                            "silently contradicting an older block."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["project", "global"],
                        "description": (
                            "Where to write. Defaults to `project`. Use "
                            "`global` only for facts true of the user "
                            "everywhere — preferences, prose style, standing "
                            "rules — not for anything about this codebase."
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
    scope = arguments.get("scope") or ("project" if name == "agd_memory_save" else "all")
    files: list[Path] = []

    if name == "agd_memory_save":
        mem = _ensure_memory_file(scope) or _bootstrap_memory_file(scope)
    else:
        files = _memory_files(scope)
        if not files:
            return [TextContent(
                type="text",
                text=(
                    f"No AGD memory file for scope '{scope}' "
                    f"(project: {memory_path()}). "
                    "Save a first entry via agd_memory_save to bootstrap one."
                ),
            )]

    if name == "agd_memory_toc":
        entries = []
        global_file = memory_path("global")
        for path in files:
            is_global = path == global_file
            for e in _toc_entries(
                path,
                kind=arguments.get("kind"),
                status=arguments.get("status"),
                since=arguments.get("since"),
            ):
                if is_global:
                    # Every layer carries the same `@h2 User/Feedback/...`
                    # scaffold; listing it twice is pure noise.
                    if not e["kind"].startswith("x-"):
                        continue
                    e["scope"] = "global"
                entries.append(e)
        return [TextContent(
            type="text",
            text=json.dumps(entries, ensure_ascii=False, separators=(",", ":")),
        )]

    if name == "agd_memory_get":
        # Accept either `id` (single, legacy) or `ids` (list).
        ids: list[str] = []
        if arguments.get("ids"):
            ids = list(arguments["ids"])
        elif arguments.get("id"):
            ids = [arguments["id"]]
        else:
            return [TextContent(type="text", text="provide either 'id' or 'ids'")]
        cli_ids = [f"#{x.lstrip('#')}" for x in ids]
        flags = []
        if arguments.get("with_backlinks"):
            flags.append("--with-backlinks")
        if arguments.get("follow_refs"):
            flags.append("--follow-refs")
            depth = arguments.get("depth")
            if isinstance(depth, int) and depth >= 1:
                flags += ["--depth", str(depth)]
        # An id lives in exactly one layer, and `agd get` fails the whole
        # call on an unknown id — so ask each layer and keep what answers.
        chunks, errors = [], []
        for path in files:
            try:
                chunks.append(_run([str(AGD_BIN), "get", str(path), *cli_ids, *flags]))
            except subprocess.CalledProcessError as e:
                errors.append((e.stderr or "").strip())
        if chunks:
            return [TextContent(type="text", text="\n".join(chunks))]
        return [TextContent(type="text", text=f"get failed: {'; '.join(errors)}")]

    if name == "agd_memory_search":
        hits, errors = [], []
        for path in files:
            cmd = [str(AGD_BIN), "search", str(path), arguments["query"], "--json"]
            if arguments.get("ignore_case", True):
                cmd.append("-i")
            if arguments.get("kind"):
                cmd += ["--kind", arguments["kind"]]
            try:
                out = _run(cmd).strip()
                if out:
                    parsed = json.loads(out)
                    if isinstance(parsed, list):
                        hits.extend(parsed)
            except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
                errors.append(str(getattr(e, "stderr", e) or e).strip())
        if hits or not errors:
            return [TextContent(
                type="text",
                text=json.dumps(hits, ensure_ascii=False, separators=(",", ":")),
            )]
        return [TextContent(type="text", text=f"search failed: {'; '.join(errors)}")]

    if name == "agd_memory_save":
        try:
            result = save_block(
                AGD_BIN,
                mem,
                arguments["kind"],
                arguments["id"],
                arguments["content"],
                desc=arguments.get("desc"),
                refs=arguments.get("refs"),
                status=arguments.get("status"),
                supersedes=arguments.get("supersedes"),
            )
        except SaveRejected as e:
            return [TextContent(type="text", text=f"save rejected: {e}")]
        except subprocess.CalledProcessError as e:
            return [TextContent(
                type="text", text=f"save failed: {(e.stderr or '').strip()}",
            )]
        return [TextContent(type="text", text=result)]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
