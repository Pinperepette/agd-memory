#!/usr/bin/env python3
"""Compare v3 (no caching) vs v3+cache pytest runs.

Reads:
  results/run_pytest_v3_agd.json        — v3 original (agd-graph only)
  results/cache_run/<instance_id>.json  — v3+cache (agd-graph + grep-agent)

Prints per-adapter aggregate diff and per-task table.
"""
from __future__ import annotations
import json, glob
from pathlib import Path
from statistics import median

# Haiku 4.5 pricing per Mtok
P_IN = 0.80
P_OUT = 4.00
P_CW = P_IN * 1.25
P_CR = P_IN * 0.10


def cost(api: dict) -> float:
    return (
        api.get("input_tokens", 0) * P_IN
        + api.get("cache_write", 0) * P_CW
        + api.get("cache_read", 0) * P_CR
        + api.get("output_tokens", 0) * P_OUT
    ) / 1_000_000


def load_v3():
    d = json.load(open("results/run_pytest_v3_agd.json"))
    by_id = {}
    for r in d.get("results", []):
        if r.get("adapter") == "agd-graph":
            by_id[r["instance_id"]] = r
    return by_id


def load_cache():
    rows = []
    for fp in sorted(glob.glob("results/cache_run/*.json")):
        d = json.load(open(fp))
        rows.extend(d.get("results", []))
    by = {"agd-graph": {}, "grep-agent": {}}
    for r in rows:
        a = r.get("adapter")
        if a in by:
            by[a][r["instance_id"]] = r
    return by


def fmt_pct(a, b):
    if not b:
        return "n/a"
    return f"{(a-b)/b*100:+.1f}%"


def metric(rows, key, fn=None):
    vals = []
    for r in rows:
        v = r
        for k in key.split("."):
            v = v.get(k) if isinstance(v, dict) else None
            if v is None:
                break
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return fn(vals) if fn else sum(vals) / len(vals)


def summary(rows, name):
    if not rows:
        return f"  {name}: no rows"
    fo = metric(rows, "oracle.function_overlap")
    fl = metric(rows, "oracle.file_overlap")
    lj = metric(rows, "oracle.line_jaccard")
    raf_med = metric(rows, "raf", median)
    tc = metric(rows, "tool_calls")
    costs = [cost(r.get("api", {})) for r in rows]
    return (
        f"  {name}: n={len(rows)} | function_overlap={fo:.3f} | "
        f"file_overlap={fl:.3f} | line_jaccard={lj:.3f} | "
        f"RAF_med={raf_med:.0f} | tool_calls={tc:.1f} | "
        f"$total={sum(costs):.3f}"
    )


def main():
    v3 = load_v3()
    cache = load_cache()

    print("=" * 80)
    print("v3 (no cache) vs v3+cache — pytest 10 tasks, Haiku 4.5")
    print("=" * 80)

    print("\n## agd-graph aggregate")
    print(summary(list(v3.values()), "v3 (no cache)     "))
    print(summary(list(cache["agd-graph"].values()), "v3+cache (agd)    "))

    print("\n## grep-agent aggregate (no v3 baseline — first run with caching)")
    print(summary(list(cache["grep-agent"].values()), "v3+cache (grep)   "))

    # cost diff per task
    print("\n## per-task cost (agd-graph): v3 vs v3+cache")
    print(f"  {'instance':<32s} {'$ v3':>8s} {'$ cache':>10s} {'delta':>10s}  fo_v3->cache")
    tot_v3 = tot_c = 0.0
    for iid in sorted(v3.keys()):
        c_v3 = cost(v3[iid].get("api", {}))
        c_c = cost(cache["agd-graph"].get(iid, {}).get("api", {})) if iid in cache["agd-graph"] else None
        fo_v3 = v3[iid].get("oracle", {}).get("function_overlap", 0)
        fo_c = cache["agd-graph"].get(iid, {}).get("oracle", {}).get("function_overlap", 0) if iid in cache["agd-graph"] else None
        if c_c is None:
            print(f"  {iid:<32s} {c_v3:>8.4f}  (missing)")
            continue
        tot_v3 += c_v3
        tot_c += c_c
        fo_str = f"{fo_v3:.2f} -> {fo_c:.2f}"
        print(f"  {iid:<32s} {c_v3:>8.4f} {c_c:>10.4f} {fmt_pct(c_c, c_v3):>10s}  {fo_str}")

    print(f"\n  totals (agd-graph): ${tot_v3:.3f} -> ${tot_c:.3f}  delta={fmt_pct(tot_c, tot_v3)}")

    # also: grep-agent with cache totals (no historical baseline in this file)
    grep_cost = sum(cost(r.get("api", {})) for r in cache["grep-agent"].values())
    print(f"\n  totals (grep-agent v3+cache): ${grep_cost:.3f} (n={len(cache['grep-agent'])})")


if __name__ == "__main__":
    main()
