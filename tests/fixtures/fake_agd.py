#!/usr/bin/env python3
"""Test double for the `agd` CLI.

Reads a dataset from `$FAKE_AGD_FIXTURE` (a JSON file) and emulates the
subcommands used by `mcp_servers/server.py` and the recall hook. Each
invocation is appended as one JSON line to `$FAKE_AGD_LOG` so tests can
assert on the arguments passed (notably the `--op` JSON for `edit`).

Dataset shape:
    {
        "blocks": [
            {"id": "...", "kind": "...", "desc": "...", "body": "...", "refs": [...]},
            ...
        ],
        "validate_ok": true,            # optional, default true
        "edit_fails": false,            # optional, makes `edit` exit 2 with stderr
        "get_fails": false              # optional, makes `get` exit 2
    }
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _log_invocation(argv: list[str]) -> None:
    path = os.environ.get("FAKE_AGD_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")
    except OSError:
        pass


def _load_fixture() -> dict:
    path = os.environ.get("FAKE_AGD_FIXTURE")
    if not path or not Path(path).is_file():
        return {"blocks": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _filter_kind(blocks: list[dict], kind: str | None) -> list[dict]:
    if not kind:
        return blocks
    return [b for b in blocks if b.get("kind") == kind]


def _block_to_agd_json(b: dict) -> dict:
    attrs: dict = {}
    if b.get("desc"):
        attrs["desc"] = b["desc"]
    refs = b.get("refs") or []
    if refs:
        attrs["refs"] = ",".join(f"#{r.lstrip('#')}" for r in refs)
    extra = b.get("attrs") or {}
    attrs.update(extra)
    return {
        "id": b["id"],
        "kind": b.get("kind", ""),
        "attrs": attrs,
        "content": {"type": "fenced", "value": b.get("body", "")},
    }


def _block_to_agd_text(b: dict) -> str:
    attrs_parts = []
    if b.get("desc"):
        attrs_parts.append(f'desc="{b["desc"]}"')
    refs = b.get("refs") or []
    if refs:
        attrs_parts.append(
            'refs="' + ",".join(f"#{r.lstrip('#')}" for r in refs) + '"'
        )
    head = f"@{b.get('kind', '')}"
    if attrs_parts:
        head += " " + " ".join(attrs_parts)
    head += f" [#{b['id']}]"
    body = b.get("body", "")
    return f"{head}\n~~~\n{body}\n~~~\n"


def cmd_parse(args: list[str], data: dict) -> int:
    payload = {"blocks": [_block_to_agd_json(b) for b in data.get("blocks", [])]}
    sys.stdout.write(json.dumps(payload))
    return 0


def cmd_ids(args: list[str], data: dict) -> int:
    as_json = "--json" in args
    kind: str | None = None
    if "--kind" in args:
        kind = args[args.index("--kind") + 1]
    blocks = _filter_kind(data.get("blocks", []), kind)
    if as_json:
        out = [
            {"id": b["id"], "kind": b.get("kind", ""), "desc": b.get("desc")}
            for b in blocks
        ]
        sys.stdout.write(json.dumps(out))
    else:
        for b in blocks:
            desc = b.get("desc") or ""
            sys.stdout.write(f"{b['id']}\t{b.get('kind', '')}\t{desc}\n")
    return 0


def cmd_get(args: list[str], data: dict) -> int:
    if data.get("get_fails"):
        sys.stderr.write("fake_agd: get failed\n")
        return 2
    as_json = "--json" in args
    ids = [a.lstrip("#") for a in args if a.startswith("#")]
    by_id = {b["id"]: b for b in data.get("blocks", [])}
    chosen = [by_id[i] for i in ids if i in by_id]
    if as_json:
        sys.stdout.write(json.dumps([_block_to_agd_json(b) for b in chosen]))
    else:
        sys.stdout.write("".join(_block_to_agd_text(b) for b in chosen))
    return 0


def cmd_search(args: list[str], data: dict) -> int:
    as_json = "--json" in args
    kind: str | None = None
    if "--kind" in args:
        kind = args[args.index("--kind") + 1]
    positional = [
        a for a in args[1:]
        if not a.startswith("-") and a != "--json" and a != "-i"
    ]
    # positional[0] is the file path, [1] is the query
    query = positional[1] if len(positional) > 1 else ""
    ignore_case = "-i" in args
    hits = []
    for b in _filter_kind(data.get("blocks", []), kind):
        body = b.get("body", "")
        hay = body.lower() if ignore_case else body
        needle = query.lower() if ignore_case else query
        if needle and needle in hay:
            idx = hay.find(needle)
            start = max(0, idx - 40)
            end = min(len(body), idx + len(needle) + 40)
            hits.append({
                "id": b["id"],
                "kind": b.get("kind", ""),
                "desc": b.get("desc"),
                "excerpt": body[start:end],
            })
    if as_json:
        sys.stdout.write(json.dumps(hits))
    else:
        for h in hits:
            sys.stdout.write(f"{h['id']}\t{h['excerpt']}\n")
    return 0


def cmd_edit(args: list[str], data: dict) -> int:
    if data.get("edit_fails"):
        sys.stderr.write("fake_agd: edit failed\n")
        return 2
    return 0


def cmd_validate(args: list[str], data: dict) -> int:
    return 0 if data.get("validate_ok", True) else 1


HANDLERS = {
    "parse": cmd_parse,
    "ids": cmd_ids,
    "get": cmd_get,
    "search": cmd_search,
    "edit": cmd_edit,
    "validate": cmd_validate,
}


def main() -> int:
    argv = sys.argv[1:]
    _log_invocation(argv)
    if not argv:
        sys.stderr.write("fake_agd: no subcommand\n")
        return 2
    sub = argv[0]
    handler = HANDLERS.get(sub)
    if handler is None:
        sys.stderr.write(f"fake_agd: unknown subcommand {sub!r}\n")
        return 2
    data = _load_fixture()
    return handler(argv, data)


if __name__ == "__main__":
    raise SystemExit(main())
