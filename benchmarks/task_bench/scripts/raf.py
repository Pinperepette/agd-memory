#!/usr/bin/env python3
"""Retrieval Amplification Factor (RAF).

RAF = tokens_loaded_into_context / tokens_essential_for_correct_fix

`tokens_essential` is derived post-hoc from the gold patch: for each
hunk, the enclosing function/class is "essential context". RAF is the
oracle metric that bridges token-shipped (S0–S5) to task correctness
without depending on the model's behaviour.

Why this metric: a strategy that achieves correctness by loading 100x
more context than necessary is not really retrieving — it is hoping.
RAF makes that visible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import tiktoken

ENC = tiktoken.get_encoding("cl100k_base")


def _tok(s: str) -> int:
    return len(ENC.encode(s))


@dataclass
class EssentialContext:
    """Files and approximate spans needed to construct the gold patch."""
    file_to_lines: dict[str, set[int]]   # file path -> set of line numbers touched
    file_to_text: dict[str, str]          # file path -> essential function body
    total_tokens: int


def parse_gold_patch_files(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Map each touched file -> list of (old_start, old_count) hunk ranges.

    Standard unified-diff: lines starting with `--- a/path` introduce a
    file, then `@@ -old_start,old_count +new_start,new_count @@` per
    hunk. We use the OLD side because we resolve essential context
    against the pre-patch repo (the base_commit).
    """
    out: dict[str, list[tuple[int, int]]] = {}
    cur: str | None = None
    file_re = re.compile(r"^\+\+\+ b/(.+)$")
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
    for line in patch.splitlines():
        m = file_re.match(line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        if cur is None:
            continue
        m = hunk_re.match(line)
        if m:
            old_start = int(m.group(1))
            old_count = int(m.group(2) or 1)
            out[cur].append((old_start, old_count))
    return out


def _enclosing_function_span(file_text: str, line_no: int) -> tuple[int, int] | None:
    """Heuristic: walk back from line_no to nearest def/class at any indent,
    forward to next def/class at the same-or-lower indent. Pure indentation
    analysis — no AST — so it survives syntax errors and partial parses.
    Returns (start_line, end_line) inclusive, 1-indexed, or None.
    """
    lines = file_text.splitlines()
    if not lines or line_no < 1 or line_no > len(lines):
        return None
    idx = line_no - 1

    def_re = re.compile(r"^(\s*)(def|class|async def)\s")

    start = None
    start_indent = None
    for i in range(idx, -1, -1):
        m = def_re.match(lines[i])
        if m:
            start = i
            start_indent = len(m.group(1))
            break
    if start is None:
        return None

    end = len(lines) - 1
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if not line.strip():
            continue
        ind = len(line) - len(line.lstrip())
        if ind <= (start_indent or 0) and def_re.match(line):
            end = j - 1
            break
    return (start + 1, end + 1)


def compute_essential(
    repo_root: Path, gold_patch: str
) -> EssentialContext:
    """For each touched file, collect the bodies of the enclosing functions
    of every changed hunk. Token count is over the union of those bodies.

    Edge cases:
    - File added by patch (no pre-patch text): we skip it for essential
      purposes — there is nothing to read first.
    - File deleted: same, no pre-patch context to load.
    - Hunk outside any function (module-level changes): we take a
      window of +/- 30 lines around the hunk.
    """
    file_hunks = parse_gold_patch_files(gold_patch)
    file_to_lines: dict[str, set[int]] = {}
    file_to_text: dict[str, str] = {}
    total_tokens = 0

    for rel_path, hunks in file_hunks.items():
        path = repo_root / rel_path
        if not path.exists():
            continue  # added file
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue

        spans: list[tuple[int, int]] = []
        for old_start, old_count in hunks:
            mid = old_start + max(old_count // 2, 0)
            span = _enclosing_function_span(text, mid)
            if span is None:
                lo = max(1, old_start - 30)
                hi = min(len(text.splitlines()), old_start + old_count + 30)
                span = (lo, hi)
            spans.append(span)

        merged = _merge_spans(spans)
        lines = text.splitlines()
        chunks = []
        line_set: set[int] = set()
        for lo, hi in merged:
            chunk = "\n".join(lines[lo - 1:hi])
            chunks.append(chunk)
            line_set.update(range(lo, hi + 1))

        body = "\n\n".join(chunks)
        file_to_lines[rel_path] = line_set
        file_to_text[rel_path] = body
        total_tokens += _tok(body)

    return EssentialContext(file_to_lines, file_to_text, total_tokens)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    s = sorted(spans)
    if not s:
        return []
    out = [s[0]]
    for lo, hi in s[1:]:
        plo, phi = out[-1]
        if lo <= phi + 1:
            out[-1] = (plo, max(phi, hi))
        else:
            out.append((lo, hi))
    return out


def raf(loaded_tokens: int, essential_tokens: int) -> float:
    if essential_tokens <= 0:
        return float("inf")
    return loaded_tokens / essential_tokens


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--patch-file", required=True, type=Path)
    args = ap.parse_args()
    patch = args.patch_file.read_text()
    ec = compute_essential(args.repo, patch)
    print(json.dumps({
        "essential_files": list(ec.file_to_lines.keys()),
        "essential_total_tokens": ec.total_tokens,
        "per_file_lines": {k: sorted(v) for k, v in ec.file_to_lines.items()},
    }, indent=2))
