#!/usr/bin/env python3
"""CLI front-end to the shared memory writer.

The SessionEnd distiller runs as a headless `claude -p` pass and needs a
way to write blocks. It used to carry its own copy of the write logic;
this entry point exists so it goes through `lib/agd_memory_write.py`
instead — the same validation, locking, ref extraction and lifecycle
stamping the MCP server uses.

Usage:
    agd_mem_save.py <kind> <id> --desc "one-line summary" --content-file BODY
    agd_mem_save.py <kind> <id> --desc "..." --content "inline body"

Options mirror the MCP tool: --refs, --status, --supersedes, --scope.
Exit codes: 0 written, 1 rejected or failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from agd_memory_paths import global_memory_file, project_memory_file  # noqa: E402
from agd_memory_write import (  # noqa: E402
    VALID_STATUS,
    SaveRejected,
    bootstrap_memory_file,
    save_block,
)


def _csv(value: str | None) -> list[str]:
    return [v.strip().lstrip("#") for v in (value or "").split(",") if v.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kind", help="x-user | x-feedback | x-project | x-reference")
    ap.add_argument("id", help="stable kebab-case block id")
    ap.add_argument("--desc", default=None, help="one-line TOC summary")
    body = ap.add_mutually_exclusive_group(required=True)
    body.add_argument("--content", help="body text, inline")
    body.add_argument("--content-file", type=Path, help="file holding the body")
    ap.add_argument("--refs", help="comma-separated ids to link to")
    ap.add_argument("--status", choices=VALID_STATUS)
    ap.add_argument("--supersedes", help="comma-separated ids this replaces")
    ap.add_argument("--scope", choices=("project", "global"), default="project")
    args = ap.parse_args(argv)

    if args.content_file is not None:
        try:
            content = args.content_file.read_text(encoding="utf-8")
        except OSError as e:
            print(f"cannot read --content-file: {e}", file=sys.stderr)
            return 1
    else:
        content = args.content

    mem = (
        global_memory_file() if args.scope == "global" else project_memory_file()
    )
    if not mem.is_file():
        bootstrap_memory_file(mem)

    agd_bin = Path(os.environ.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))
    if not agd_bin.is_file():
        print(f"agd binary not found at {agd_bin}", file=sys.stderr)
        return 1

    try:
        print(save_block(
            agd_bin, mem, args.kind, args.id, content,
            desc=args.desc,
            refs=_csv(args.refs),
            status=args.status,
            supersedes=_csv(args.supersedes),
        ))
    except SaveRejected as e:
        print(f"save rejected: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"save failed: {(e.stderr or '').strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
