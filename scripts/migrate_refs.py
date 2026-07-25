#!/usr/bin/env python3
"""Lift prose citations into real `refs=` graph edges.

Blocks routinely cite each other in prose — `Rif. #catalogo-unico,
#produzione-swap` — which `agd backlinks` and `agd get --follow-refs`
cannot see. `server.py` now extracts those on every save, but memory
files written before that keep their edges in prose only. This walks
existing files and rewrites the `refs=` attribute in place.

Only ids that resolve to a block in the same file become edges, so `#1`
issue numbers and `#fff` colour literals are ignored rather than turned
into dangling references.

Usage:
    migrate_refs.py                 # dry run over every project memory
    migrate_refs.py --apply         # write the changes
    migrate_refs.py PATH [PATH...]  # restrict to specific files
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from pathlib import Path

# A block header: `@x-project desc="..." refs="#a" [#the-id]`
_HEADER = re.compile(r"^@(?P<kind>[A-Za-z][\w-]*)(?P<attrs>.*?)\[#(?P<id>[^\]]+)\]\s*$")
_REF = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")
_REFS_ATTR = re.compile(r'\s*refs="[^"]*"')


def _blocks(lines: list[str]) -> list[tuple[int, str, str, str]]:
    """(line index, kind, attrs, id) for every block header in the file."""
    out = []
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if m:
            out.append((i, m.group("kind"), m.group("attrs"), m.group("id")))
    return out


def _body_between(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start + 1:end])


def _existing_refs(attrs: str) -> list[str]:
    m = re.search(r'refs="([^"]*)"', attrs)
    if not m:
        return []
    return [r.strip().lstrip("#") for r in m.group(1).split(",") if r.strip().lstrip("#")]


def plan_file(path: Path) -> list[tuple[str, list[str], list[str]]]:
    """Return (block_id, before_refs, after_refs) for blocks that change."""
    lines = path.read_text(encoding="utf-8").splitlines()
    headers = _blocks(lines)
    known = {h[3] for h in headers}
    changes = []
    for n, (idx, _kind, attrs, bid) in enumerate(headers):
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        body = _body_between(lines, idx, end)
        before = _existing_refs(attrs)
        after = list(before)
        for m in _REF.finditer(body):
            rid = m.group(1)
            if rid != bid and rid in known and rid not in after:
                after.append(rid)
        if after != before:
            changes.append((bid, before, after))
    return changes


def apply_file(path: Path, changes: list[tuple[str, list[str], list[str]]]) -> None:
    """Rewrite the `refs=` attribute on each changed header, in place."""
    wanted = {bid: after for bid, _before, after in changes}
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _HEADER.match(line.rstrip("\n"))
        if not m or m.group("id") not in wanted:
            continue
        refs = ",".join(f"#{r}" for r in wanted[m.group("id")])
        attrs = _REFS_ATTR.sub("", m.group("attrs")).rstrip()
        lines[i] = f'@{m.group("kind")}{attrs} refs="{refs}" [#{m.group("id")}]\n'
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="memory files (default: all projects)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in args.paths] or [
        Path(p) for p in sorted(glob.glob(
            os.path.expanduser("~/.claude/projects/*/memory/memory.agd")
        ))
    ]

    total_edges = total_blocks = total_files = 0
    for path in paths:
        if not path.is_file():
            print(f"skip (not a file): {path}", file=sys.stderr)
            continue
        try:
            changes = plan_file(path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"skip ({e}): {path}", file=sys.stderr)
            continue
        if not changes:
            continue
        total_files += 1
        total_blocks += len(changes)
        print(f"\n{path}")
        for bid, before, after in changes:
            total_edges += len(after) - len(before)
            added = [r for r in after if r not in before]
            print(f"  #{bid}: +{', '.join('#' + r for r in added)}")
        if args.apply:
            apply_file(path, changes)

    verb = "added" if args.apply else "would add"
    print(f"\n{verb} {total_edges} edge(s) across {total_blocks} block(s) "
          f"in {total_files} file(s)")
    if not args.apply and total_edges:
        print("dry run — re-run with --apply to write (a .bak is kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
