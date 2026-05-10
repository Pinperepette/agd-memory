#!/usr/bin/env python3
"""Aggregate task-bench results into a flat summary.

Reads a run.json produced by bench_task.py and emits:

- per-adapter: median/p95 RAF, mean function_overlap, mean off_target,
  mean tool_calls, total $.
- per-task: which adapter "won" on (function_overlap, RAF) jointly.
- a CDF of RAF per adapter (5 buckets) — the headline plot the README
  will eventually carry.

The dollar prices use the same Haiku-4.5 list as bench_cost.py. If we
add Sonnet later, extend PRICING.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

PRICING = {
    "claude-haiku-4-5": {
        "input": 1.00,
        "cache_write_5m": 1.25,
        "cache_read": 0.10,
        "output": 5.00,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_write_5m": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
}


def cost_usd(api: dict, model: str) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return (
        api.get("input_tokens", 0) * p["input"]
        + api.get("cache_write", 0) * p["cache_write_5m"]
        + api.get("cache_read", 0) * p["cache_read"]
        + api.get("output_tokens", 0) * p["output"]
    ) / 1_000_000


def summarize(run: dict) -> dict:
    rows = [r for r in run["results"] if "error" not in r]
    errors = [r for r in run["results"] if "error" in r]

    by_adapter: dict[str, list[dict]] = {}
    for r in rows:
        by_adapter.setdefault(r["adapter"], []).append(r)

    per_adapter = []
    for name, rs in by_adapter.items():
        rafs = [r["raf"] for r in rs if r["raf"] != float("inf")]
        per_adapter.append({
            "adapter": name,
            "n": len(rs),
            "raf_median": round(statistics.median(rafs), 2) if rafs else None,
            "raf_p95": round(_p(rafs, 0.95), 2) if rafs else None,
            "raf_mean": round(statistics.mean(rafs), 2) if rafs else None,
            "function_overlap_mean": round(statistics.mean(r["oracle"]["function_overlap"] for r in rs), 3),
            "file_overlap_mean": round(statistics.mean(r["oracle"]["file_overlap"] for r in rs), 3),
            "line_jaccard_mean": round(statistics.mean(r["oracle"]["line_jaccard"] for r in rs), 3),
            "off_target_mean": round(statistics.mean(len(r["oracle"]["off_target_files"]) for r in rs), 2),
            "tool_calls_mean": round(statistics.mean(r["tool_calls"] for r in rs), 1),
            "loaded_tokens_mean": round(statistics.mean(r["loaded_tokens"] for r in rs)),
            "essential_tokens_mean": round(statistics.mean(r["essential_tokens"] for r in rs)),
            "total_cost_usd": round(sum(cost_usd(r["api"], r["model"]) for r in rs), 4),
            "elapsed_s_mean": round(statistics.mean(r["elapsed_s"] for r in rs), 2),
        })

    # CDF of RAF: count fraction of (task,adapter) below each threshold
    thresholds = [2, 5, 10, 50, 200]
    cdf = []
    for t in thresholds:
        row = {"raf_le": t}
        for name, rs in by_adapter.items():
            rafs = [r["raf"] for r in rs if r["raf"] != float("inf")]
            if rafs:
                row[name] = round(sum(1 for v in rafs if v <= t) / len(rafs), 3)
            else:
                row[name] = None
        cdf.append(row)

    # Per-task winner: lowest RAF among adapters with function_overlap > 0
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["instance_id"], []).append(r)
    winners = []
    for tid, rs in by_task.items():
        correct = [r for r in rs if r["oracle"]["function_overlap"] > 0]
        if correct:
            winner = min(correct, key=lambda r: r["raf"])
            winners.append({
                "instance_id": tid,
                "winner_adapter": winner["adapter"],
                "winner_raf": winner["raf"],
                "winner_function_overlap": winner["oracle"]["function_overlap"],
            })
        else:
            winners.append({
                "instance_id": tid,
                "winner_adapter": None,
                "note": "no adapter produced a non-zero function_overlap",
            })

    return {
        "model": run.get("model"),
        "n_instances": run.get("n_instances"),
        "n_rows": len(rows),
        "n_errors": len(errors),
        "errors": [{"instance_id": e["instance_id"], "adapter": e["adapter"], "error": e["error"]} for e in errors],
        "per_adapter": per_adapter,
        "raf_cdf": cdf,
        "per_task_winners": winners,
    }


def _p(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def to_markdown(summary: dict) -> str:
    lines = []
    lines.append(f"# task-bench v0 — {summary.get('model','?')}")
    lines.append("")
    lines.append(f"- instances: **{summary.get('n_instances')}**, rows: {summary.get('n_rows')}, errors: {summary.get('n_errors')}")
    lines.append("")
    lines.append("## per adapter")
    lines.append("")
    lines.append("| adapter | n | RAF median | RAF p95 | function_overlap | file_overlap | line_jaccard | off_target | tool_calls | $ total | s/run |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for a in summary["per_adapter"]:
        lines.append(
            f"| `{a['adapter']}` | {a['n']} | {a['raf_median']} | {a['raf_p95']} | "
            f"{a['function_overlap_mean']:.2f} | {a['file_overlap_mean']:.2f} | "
            f"{a['line_jaccard_mean']:.2f} | {a['off_target_mean']} | "
            f"{a['tool_calls_mean']} | ${a['total_cost_usd']:.3f} | {a['elapsed_s_mean']} |"
        )
    lines.append("")
    lines.append("## RAF CDF (fraction of runs with RAF ≤ threshold)")
    lines.append("")
    if summary["raf_cdf"]:
        adapters = [k for k in summary["raf_cdf"][0].keys() if k != "raf_le"]
        lines.append("| RAF ≤ | " + " | ".join(f"`{a}`" for a in adapters) + " |")
        lines.append("|---:" + "|---:" * len(adapters) + "|")
        for row in summary["raf_cdf"]:
            cells = [str(row["raf_le"])] + [f"{row.get(a):.2f}" if row.get(a) is not None else "—" for a in adapters]
            lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## per-task winners (lowest RAF among adapters with function_overlap > 0)")
    lines.append("")
    for w in summary["per_task_winners"]:
        if w.get("winner_adapter"):
            lines.append(f"- `{w['instance_id']}` → **{w['winner_adapter']}** (RAF={w['winner_raf']}, fn={w['winner_function_overlap']:.2f})")
        else:
            lines.append(f"- `{w['instance_id']}` → no adapter solved it")
    lines.append("")
    if summary.get("errors"):
        lines.append("## errors")
        for e in summary["errors"]:
            lines.append(f"- {e['instance_id']} / {e['adapter']}: `{e['error']}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_file", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None, help="also write a markdown summary")
    args = ap.parse_args()

    run = json.loads(args.run_file.read_text())
    summary = summarize(run)

    out = args.out or args.run_file.with_suffix(".summary.json")
    out.write_text(json.dumps(summary, indent=2))
    md = args.md or args.run_file.with_suffix(".summary.md")
    md.write_text(to_markdown(summary))
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {out}")
    print(f"wrote {md}")


if __name__ == "__main__":
    main()
