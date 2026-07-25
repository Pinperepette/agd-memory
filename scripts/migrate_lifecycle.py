#!/usr/bin/env python3
"""Backfill `created` / `updated` / `status` from prose.

Before lifecycle attributes existed, blocks recorded their date and state
inside the description — `desc="FATTO 2026-06-25: dedup Cod+Extra"`.
Measured on the real corpus: 322 such date strings, none queryable. This
lifts the first ISO date it finds into `created`/`updated`, and maps a
small set of leading state markers onto `status`.

The markers are deliberately conservative and anchored to the start of
the desc, so a date mentioned mid-sentence never silently becomes the
block's creation date and the word "fatto" inside a paragraph is not
mistaken for a state. Blocks that already carry the attribute are left
alone. Prose is never rewritten — only attributes are added.

Usage:
    migrate_lifecycle.py                 # dry run over every project memory
    migrate_lifecycle.py --apply         # write the changes
    migrate_lifecycle.py PATH [PATH...]  # restrict to specific files
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from pathlib import Path

_HEADER = re.compile(r"^@(?P<kind>[A-Za-z][\w-]*)(?P<attrs>.*?)\[#(?P<id>[^\]]+)\]\s*$")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# Anchored at the start of the description, case-insensitive.
_STATUS_MARKERS = (
    (re.compile(r"^\s*(fatto|done|completato|risolto|closed)\b", re.I), "done"),
    (re.compile(r"^\s*(da\s*fare|todo|open|aperto|in\s*corso|wip)\b", re.I), "open"),
)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', attrs) or re.search(rf"{name}=(\S+)", attrs)
    return m.group(1) if m else None


def _infer(desc: str, body: str) -> dict[str, str]:
    """Attributes inferable from this block's prose, if any."""
    found: dict[str, str] = {}
    for pattern, state in _STATUS_MARKERS:
        if pattern.match(desc or ""):
            found["status"] = state
            break
    m = _ISO_DATE.search(desc or "") or _ISO_DATE.search(body or "")
    if m:
        y, mo, d = m.groups()
        # Reject impossible dates rather than writing a bad attribute.
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            found["created"] = f"{y}-{mo}-{d}"
    return found


def plan_file(path: Path) -> list[tuple[str, dict[str, str]]]:
    """Return (block_id, attributes_to_add) for blocks that change."""
    lines = path.read_text(encoding="utf-8").splitlines()
    headers = [
        (i, m) for i, line in enumerate(lines) if (m := _HEADER.match(line))
    ]
    changes = []
    for n, (idx, m) in enumerate(headers):
        kind = m.group("kind")
        if not kind.startswith("x-"):
            continue  # headings and meta have no lifecycle
        attrs = m.group("attrs")
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        body = "\n".join(lines[idx + 1:end])
        inferred = _infer(_attr(attrs, "desc") or "", body)
        add = {k: v for k, v in inferred.items() if not _attr(attrs, k)}
        # `updated` mirrors `created` when we have nothing better: the
        # block has not been touched since, as far as the file knows.
        if "created" in add and not _attr(attrs, "updated"):
            add["updated"] = add["created"]
        if add:
            changes.append((m.group("id"), add))
    return changes


def apply_file(path: Path, changes: list[tuple[str, dict[str, str]]]) -> None:
    wanted = dict(changes)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _HEADER.match(line.rstrip("\n"))
        if not m or m.group("id") not in wanted:
            continue
        extra = "".join(
            f" {k}={v}" for k, v in sorted(wanted[m.group("id")].items())
        )
        attrs = m.group("attrs").rstrip()
        lines[i] = f'@{m.group("kind")}{attrs}{extra} [#{m.group("id")}]\n'
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--verbose", action="store_true", help="list every block")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in args.paths] or [
        Path(p) for p in sorted(glob.glob(
            os.path.expanduser("~/.claude/projects/*/memory/memory.agd")
        ))
    ]

    stats = {"created": 0, "status": 0}
    total_blocks = total_files = 0
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
        for _bid, add in changes:
            for key in stats:
                if key in add:
                    stats[key] += 1
        if args.verbose:
            print(f"\n{path}")
            for bid, add in changes:
                pairs = " ".join(f"{k}={v}" for k, v in sorted(add.items()))
                print(f"  #{bid}: {pairs}")
        if args.apply:
            apply_file(path, changes)

    verb = "stamped" if args.apply else "would stamp"
    print(f"{verb} {total_blocks} block(s) in {total_files} file(s): "
          f"{stats['created']} dated, {stats['status']} with a status")
    if not args.apply and total_blocks:
        print("dry run — re-run with --apply to write (a .bak is kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
