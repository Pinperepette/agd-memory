#!/usr/bin/env python3
"""Measure auto-recall retrieval quality against a fixed case set.

Reports three numbers that pull in different directions, so a change has
to be judged on all of them at once:

  * hit@1     — the expected block ranked first (higher is better)
  * recall@3  — the expected block injected at all (higher is better)
  * false-inj — something injected when the answer was "nothing"
                (lower is better)
  * tokens    — total injected across the set (lower is better)

hit@1 alone is not enough to judge a change: tightening the matcher can
drop a block from rank 2 to absent without moving hit@1 at all. That is
exactly how a recall regression slipped through once already, so both
numbers are printed side by side.

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
    ap.add_argument("--fuzzy-anchor-min", type=int, default=None,
                    help="cognates required when nothing matches exactly")
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
        lambda: {"n": 0, "hit": 0, "recall": 0, "tokens": 0, "false": 0}
    )
    misses = []

    # A negative is only evidence against the corpus it was run on. Tying
    # each to one corpus hid a real false positive: "production cost of a
    # solar panel" was silent against the corpus it was filed under and
    # fired against the other one. Negatives are therefore expanded to
    # every corpus in the set — it costs nothing and it is what caught it.
    all_corpora = sorted({c["corpus"] for c in cases})
    expanded = []
    for c in cases:
        if c["expect"] is None:
            expanded.extend(dict(c, corpus=k) for k in all_corpora)
        else:
            expanded.append(c)
    cases = expanded

    for c in cases:
        mem = Path(os.path.expanduser(
            f"~/.claude/projects/{c['corpus']}/memory/memory.agd"
        ))
        if c["corpus"] not in corpora:
            corpora[c["corpus"]] = r.load_blocks(agd, mem)
        blocks = corpora[c["corpus"]]

        # Run the hook's own gate first. Skipping it measured the ranker
        # rather than the product: six cases never reach ranking in
        # production, two of which were being counted as hits.
        if r.should_skip(c["prompt"], env, stopwords=stop):
            chosen = []
        else:
            tokens = set(r.strip_stopwords(r.tokenize(c["prompt"]), stop))
            chosen = r.top_k_blocks(
                blocks, tokens, 3,
                min_score=r.DEFAULT_MIN_SCORE,
                min_score_ratio=r.DEFAULT_MIN_SCORE_RATIO,
                require_anchor=True,
                skip_superseded=True,
                fuzzy=not args.no_fuzzy,
                fuzzy_anchor_min=(
                    r.FUZZY_ANCHOR_MIN if args.fuzzy_anchor_min is None
                    else args.fuzzy_anchor_min
                ),
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
        else:
            if c["expect"] in got:
                s["recall"] += 1
            if got and got[0] == c["expect"]:
                s["hit"] += 1
            else:
                misses.append((c["prompt"], c["expect"], got))

        if args.verbose:
            print(f"  {c['lang']:2s} {c['prompt'][:44]:46s} -> {got}")

    print(f"\n{'bucket':10s} {'n':>3s} {'hit@1':>7s} {'recall@3':>9s} "
          f"{'false-inj':>10s} {'tokens':>8s}")
    for bucket in ("en", "it", "negative"):
        st = stats[bucket]
        if not st["n"]:
            continue
        hit = "—" if bucket == "negative" else f"{st['hit']}/{st['n']}"
        rec = "—" if bucket == "negative" else f"{st['recall']}/{st['n']}"
        print(f"{bucket:10s} {st['n']:3d} {hit:>7s} {rec:>9s} "
              f"{st['false']:>7d}/{st['n']:<2d} {st['tokens']:>8d}")
    total = sum(st["tokens"] for st in stats.values())
    hits = stats["en"]["hit"] + stats["it"]["hit"]
    recs = stats["en"]["recall"] + stats["it"]["recall"]
    n_pos = stats["en"]["n"] + stats["it"]["n"]
    n_neg = stats["negative"]["n"]
    falses = sum(st["false"] for st in stats.values())
    print(f"{'TOTAL':10s} {sum(st['n'] for st in stats.values()):3d} "
          f"{hits}/{n_pos:<5d} {recs}/{n_pos:<7d} {falses:>7d}/{n_neg:<2d} {total:>8d}")
    if n_neg:
        # The hook fires unattended on every prompt, so a false-injection
        # rate compounds across a working day in a way a fraction hides.
        print(f"\n≈ {100 * falses / n_neg:.0f} unwanted injections per 100 "
              f"prompts that memory cannot answer")

    if misses:
        print("\nmisses:")
        for prompt, want, got in misses:
            print(f"  {prompt[:50]:52s} want={want} got={got[:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
