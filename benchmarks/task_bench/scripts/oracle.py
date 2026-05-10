#!/usr/bin/env python3
"""Patch-similarity oracle (no Docker).

Compares the model's unified-diff to the gold unified-diff at three
levels of strictness:

    file_overlap        = |model_files ∩ gold_files| / |gold_files|
    function_overlap    = |model_funcs ∩ gold_funcs| / |gold_funcs|
    line_jaccard        = Jaccard of (file, line) pairs touched
    off_target_files    = |model_files \\ gold_files|

`function_overlap` is the strongest signal short of running tests:
the model and the gold both agreed which symbol(s) needed editing.
`off_target_files` is the hallucination proxy: the model edited
something that was not part of the fix.

Caveats explicit:
- File-set overlap can be 1.0 with semantically wrong edits.
- A model that produces the *correct* fix in a different function
  scores badly here. Without test execution we cannot detect this.
- This is a *necessary* signal, not a *sufficient* one. The headline
  number for v0; real ground truth waits for the Docker oracle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from raf import _enclosing_function_span


@dataclass
class OracleResult:
    file_overlap: float
    function_overlap: float
    line_jaccard: float
    off_target_files: list[str]
    gold_files: list[str]
    model_files: list[str]
    gold_functions: list[str]
    model_functions: list[str]


def _files_in_patch(patch: str) -> list[str]:
    out = []
    for line in patch.splitlines():
        m = re.match(r"^\+\+\+ b/(.+)$", line)
        if m and m.group(1) != "/dev/null":
            out.append(m.group(1))
    return out


def _file_line_pairs(patch: str) -> set[tuple[str, int]]:
    """Set of (file, old-side line number) touched by the patch."""
    pairs: set[tuple[str, int]] = set()
    cur: str | None = None
    file_re = re.compile(r"^\+\+\+ b/(.+)$")
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
    for line in patch.splitlines():
        m = file_re.match(line)
        if m:
            cur = m.group(1) if m.group(1) != "/dev/null" else None
            continue
        if cur is None:
            continue
        m = hunk_re.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            for n in range(start, start + count):
                pairs.add((cur, n))
    return pairs


def _functions_touched(patch: str, repo_root: Path) -> set[str]:
    """Set of "file::funcname" symbols touched by hunks of the patch.

    Resolved against the pre-patch source under repo_root, using the
    same heuristic as raf.compute_essential.
    """
    syms: set[str] = set()
    cur: str | None = None
    cur_text: str | None = None
    file_re = re.compile(r"^\+\+\+ b/(.+)$")
    hunk_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
    name_re = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")

    for line in patch.splitlines():
        m = file_re.match(line)
        if m:
            cur = m.group(1) if m.group(1) != "/dev/null" else None
            cur_text = None
            if cur:
                p = repo_root / cur
                if p.exists():
                    try:
                        cur_text = p.read_text(errors="replace")
                    except OSError:
                        cur_text = None
            continue
        if cur is None or cur_text is None:
            continue
        m = hunk_re.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            mid = start + count // 2
            span = _enclosing_function_span(cur_text, mid)
            if span:
                lo, _ = span
                lines = cur_text.splitlines()
                head = lines[lo - 1] if 1 <= lo <= len(lines) else ""
                nm = name_re.match(head)
                if nm:
                    syms.add(f"{cur}::{nm.group(1)}")
                else:
                    syms.add(f"{cur}::<line{mid}>")
            else:
                syms.add(f"{cur}::<line{mid}>")
    return syms


def evaluate(model_patch: str, gold_patch: str, repo_root: Path) -> OracleResult:
    gold_files = set(_files_in_patch(gold_patch))
    model_files = set(_files_in_patch(model_patch))

    if gold_files:
        file_overlap = len(gold_files & model_files) / len(gold_files)
    else:
        file_overlap = 0.0

    gold_funcs = _functions_touched(gold_patch, repo_root)
    model_funcs = _functions_touched(model_patch, repo_root)
    if gold_funcs:
        function_overlap = len(gold_funcs & model_funcs) / len(gold_funcs)
    else:
        function_overlap = 0.0

    gold_pairs = _file_line_pairs(gold_patch)
    model_pairs = _file_line_pairs(model_patch)
    if gold_pairs or model_pairs:
        inter = len(gold_pairs & model_pairs)
        union = len(gold_pairs | model_pairs)
        line_jaccard = inter / union if union else 0.0
    else:
        line_jaccard = 0.0

    return OracleResult(
        file_overlap=file_overlap,
        function_overlap=function_overlap,
        line_jaccard=line_jaccard,
        off_target_files=sorted(model_files - gold_files),
        gold_files=sorted(gold_files),
        model_files=sorted(model_files),
        gold_functions=sorted(gold_funcs),
        model_functions=sorted(model_funcs),
    )


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    args = ap.parse_args()
    r = evaluate(args.model.read_text(), args.gold.read_text(), args.repo)
    print(json.dumps({
        "file_overlap": round(r.file_overlap, 3),
        "function_overlap": round(r.function_overlap, 3),
        "line_jaccard": round(r.line_jaccard, 3),
        "off_target_files": r.off_target_files,
        "gold_files": r.gold_files,
        "model_files": r.model_files,
    }, indent=2))
