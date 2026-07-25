#!/usr/bin/env python3
"""Consolidate two memory files that belong to the same project.

Memory is keyed by working directory, so one repository checked out at
two paths accumulates two disjoint memories. Measured on the real
install: `agd-memory` itself had two 8-block silos under
`-Users-...-Porgetti-MDF2-agd-memory` and
`-Users-...-.claude-plugins-marketplaces-agd-memory`.

This appends every `x-` block of SOURCE that DEST does not already have,
under DEST's matching `@h2` heading. Structural blocks (meta, headings)
are never copied — both files carry the same scaffold. Ids present in
both are reported and skipped: a same-id collision means two different
facts wearing one name, and picking a winner is a judgement call that
belongs to you, not to a script.

Usage:
    merge_memory.py SOURCE DEST            # dry run
    merge_memory.py SOURCE DEST --apply    # write (DEST.bak is kept)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_HEADER = re.compile(r"^@(?P<kind>[A-Za-z][\w-]*)(?P<attrs>.*?)\[#(?P<id>[^\]]+)\]\s*$")


def _parse(path: Path) -> list[dict]:
    """Split a memory file into blocks, keeping their exact source text."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    marks = [
        (i, m) for i, line in enumerate(lines) if (m := _HEADER.match(line.rstrip("\n")))
    ]
    out = []
    for n, (idx, m) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        out.append({
            "id": m.group("id"),
            "kind": m.group("kind"),
            "text": "".join(lines[idx:end]).rstrip("\n") + "\n",
        })
    return out


def plan(source: Path, dest: Path) -> tuple[list[dict], list[str]]:
    """(blocks to copy, ids that collide)."""
    dest_ids = {b["id"] for b in _parse(dest)}
    to_copy, collisions = [], []
    for b in _parse(source):
        if not b["kind"].startswith("x-"):
            continue
        (collisions if b["id"] in dest_ids else to_copy).append(
            b["id"] if b["id"] in dest_ids else b
        )
    return to_copy, collisions


def apply(dest: Path, blocks: list[dict]) -> None:
    """Insert each block after DEST's `@h2` for its kind, else append."""
    text = dest.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    for b in blocks:
        heading = f"h-{b['kind'].removeprefix('x-')}"
        at = None
        for i, line in enumerate(lines):
            m = _HEADER.match(line.rstrip("\n"))
            if m and m.group("id") == heading:
                at = i + 1
                break
        chunk = "\n" + b["text"]
        if at is None:
            lines.append(chunk)
        else:
            lines.insert(at, chunk)
    shutil.copy2(dest, dest.with_suffix(dest.suffix + ".bak"))
    dest.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.source, args.dest):
        if not p.is_file():
            print(f"not a file: {p}", file=sys.stderr)
            return 2

    to_copy, collisions = plan(args.source, args.dest)
    for b in to_copy:
        print(f"  copy #{b['id']} ({b['kind']})")
    for cid in collisions:
        print(f"  SKIP #{cid} — exists in both; merge it by hand")

    print(f"\n{'copied' if args.apply else 'would copy'} {len(to_copy)} block(s), "
          f"{len(collisions)} collision(s)")
    if args.apply and to_copy:
        apply(args.dest, to_copy)
        print(f"wrote {args.dest} (backup: {args.dest}.bak)")
    elif not args.apply and to_copy:
        print("dry run — re-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
