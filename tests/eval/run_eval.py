#!/usr/bin/env python3
"""Measure auto-recall retrieval quality against a fixed case set.

Reports three numbers that pull in different directions, so a change has
to be judged on all of them at once:

  * hit@1     — the expected block ranked first (higher is better)
  * false-inj — something injected when the answer was "nothing"
                (lower is better)
  * tokens    — total injected across the set (lower is better)

Run against the real memory files named in the case file:

    python3 tests/eval/run_eval.py             # current behaviour
    python3 tests/eval/run_eval.py --no-fuzzy  # exact-match only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_CASES = Path(__file__).resolve().parent / "crosslingual_cases.json"


def _load_recall():
    spec = importlib.util.spec_from_file_location(
        "recall_mod", _ROOT / "hooks" / "agd-memory-recall.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recall_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fuzzy", action="store_true",
                    help="disable cross-lingual cognate matching")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    r = _load_recall()
    env = {"AGD_RECALL_LANGS": "en,it"}
    if args.no_fuzzy:
        env["AGD_RECALL_NO_FUZZY"] = "1"
    agd = Path(os.path.expanduser("~/.cargo/bin/agd"))
    stop = r.resolve_stopwords(env)
    cases = json.loads(_CASES.read_text())["cases"]

    corpora: dict[str, list] = {}
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "hit": 0, "tokens": 0, "false": 0}
    )
    misses = []

    for c in cases:
        mem = Path(os.path.expanduser(
            f"~/.claude/projects/{c['corpus']}/memory/memory.agd"
        ))
        if c["corpus"] not in corpora:
            corpora[c["corpus"]] = r.load_blocks(agd, mem)
        blocks = corpora[c["corpus"]]

        tokens = set(r.strip_stopwords(r.tokenize(c["prompt"]), stop))
        chosen = r.top_k_blocks(
            blocks, tokens, 3,
            min_score=r.DEFAULT_MIN_SCORE,
            min_score_ratio=r.DEFAULT_MIN_SCORE_RATIO,
            require_anchor=True,
            skip_superseded=True,
            fuzzy=not args.no_fuzzy,
        )
        kept = r.fits_budget(chosen, r.DEFAULT_TOKEN_BUDGET * 4)
        got = [b.id for b in kept]
        cost = sum(len(r._render_one_block(b)) for b in kept) // 4

        bucket = "negative" if c["expect"] is None else c["lang"]
        s = stats[bucket]
        s["n"] += 1
        s["tokens"] += cost
        if c["expect"] is None:
            if got:
                s["false"] += 1
                misses.append((c["prompt"], "expected nothing", got))
        elif got and got[0] == c["expect"]:
            s["hit"] += 1
        else:
            misses.append((c["prompt"], c["expect"], got))

        if args.verbose:
            print(f"  {c['lang']:2s} {c['prompt'][:44]:46s} -> {got}")

    print(f"\n{'bucket':10s} {'n':>3s} {'hit@1':>7s} {'false-inj':>10s} {'tokens':>8s}")
    for bucket in ("en", "it", "negative"):
        s = stats[bucket]
        if not s["n"]:
            continue
        rate = "—" if bucket == "negative" else f"{s['hit']}/{s['n']}"
        print(f"{bucket:10s} {s['n']:3d} {rate:>7s} "
              f"{s['false']:>7d}/{s['n']:<2d} {s['tokens']:>8d}")
    total = sum(s["tokens"] for s in stats.values())
    hits = stats["en"]["hit"] + stats["it"]["hit"]
    n_pos = stats["en"]["n"] + stats["it"]["n"]
    print(f"{'TOTAL':10s} {sum(s['n'] for s in stats.values()):3d} "
          f"{hits}/{n_pos:<5d} {sum(s['false'] for s in stats.values()):>7d}/"
          f"{stats['negative']['n']:<2d} {total:>8d}")

    if misses:
        print("\nmisses:")
        for prompt, want, got in misses:
            print(f"  {prompt[:50]:52s} want={want} got={got[:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
