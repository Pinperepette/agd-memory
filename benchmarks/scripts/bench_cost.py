#!/usr/bin/env python3
"""Real-money benchmark: whole-doc cached vs selective uncached.

Makes actual Anthropic API calls with prompt caching enabled and
records `cache_creation_input_tokens` / `cache_read_input_tokens` /
`input_tokens` / `output_tokens` from each response. Then prices them
at the model's published rates.

Why this exists: scripts/bench.py reports tokens shipped, which is
misleading when one side uses prompt cache (10% read cost) and the
other does not. This benchmark answers the actual question — "how
much money do I save in dollars?" — instead of just token count.

Defaults: claude-haiku-4-5, 5 turns, corpus mem-200. Total cost of a
default run is ~$0.05.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import time
from pathlib import Path

import anthropic

# Pricing per 1M tokens. Source: anthropic.com/pricing (Haiku 4.5).
PRICING = {
    "claude-haiku-4-5": {
        "input": 1.00,
        "cache_write_5m": 1.25,
        "cache_read": 0.10,
        "output": 5.00,
    },
}


def cost_usd(usage: dict, model: str) -> float:
    p = PRICING[model]
    return (
        usage.get("input_tokens", 0) * p["input"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write_5m"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + usage.get("output_tokens", 0) * p["output"]
    ) / 1_000_000


def list_block_ids(path: Path) -> list[str]:
    out = subprocess.check_output(["agd", "ids", str(path)]).decode()
    ids = []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.startswith(("meta", "root", "h-")):
            continue
        bid = s.split("\t", 1)[0].strip()
        ids.append(bid)
    return ids


def agd_get(path: Path, bid: str) -> str:
    return subprocess.check_output(["agd", "get", str(path), f"#{bid}"]).decode()


def agd_toc(path: Path) -> str:
    return subprocess.check_output(["agd", "ids", str(path)]).decode()


def make_question(bid: str) -> str:
    desc = bid.replace("-", " ")
    return f"In una frase brevissima, riassumi la regola registrata sotto l'identificatore '{desc}'. Se non la trovi, scrivi 'non trovata'."


def run_whole_doc_cached(
    client: anthropic.Anthropic, model: str, memory_text: str,
    questions: list[str],
) -> list[dict]:
    """Each turn: cached memory prefix + fresh question."""
    rows = []
    system_msg = "Sei un assistente che risponde basandosi sulla memoria progetto fornita."
    for i, q in enumerate(questions):
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=model,
            max_tokens=120,
            system=system_msg,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": memory_text,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": q},
                ],
            }],
        )
        latency = time.perf_counter() - t0
        u = resp.usage.model_dump()
        rows.append({
            "turn": i + 1,
            "latency_s": round(latency, 3),
            **u,
            "cost_usd": round(cost_usd(u, model), 6),
        })
    return rows


def run_selective_uncached(
    client: anthropic.Anthropic, model: str, path: Path,
    block_ids: list[str], questions: list[str],
) -> list[dict]:
    """Each turn: TOC + the relevant block (uncached) + fresh question."""
    rows = []
    toc_text = agd_toc(path)
    system_msg = "Sei un assistente che risponde basandosi sulla memoria progetto fornita."
    for i, (bid, q) in enumerate(zip(block_ids, questions)):
        block_text = agd_get(path, bid)
        prefix = f"# Project memory (TOC)\n{toc_text}\n# Relevant entry\n{block_text}"
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=model,
            max_tokens=120,
            system=system_msg,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prefix},
                    {"type": "text", "text": q},
                ],
            }],
        )
        latency = time.perf_counter() - t0
        u = resp.usage.model_dump()
        rows.append({
            "turn": i + 1,
            "block_id": bid,
            "latency_s": round(latency, 3),
            **u,
            "cost_usd": round(cost_usd(u, model), 6),
        })
    return rows


def aggregate(rows: list[dict], label: str) -> dict:
    return {
        "strategy": label,
        "turns": len(rows),
        "total_input_tokens": sum(r.get("input_tokens", 0) for r in rows),
        "total_cache_write": sum(r.get("cache_creation_input_tokens", 0) for r in rows),
        "total_cache_read": sum(r.get("cache_read_input_tokens", 0) for r in rows),
        "total_output_tokens": sum(r.get("output_tokens", 0) for r in rows),
        "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in rows), 6),
        "avg_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=Path("corpora/mem-200.agd"))
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--turns", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("results/cost.json"))
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set")
    if not args.corpus.exists():
        raise SystemExit(f"missing corpus: {args.corpus}")

    client = anthropic.Anthropic()
    rng = random.Random(args.seed)
    ids = list_block_ids(args.corpus)
    picks = rng.sample(ids, min(args.turns, len(ids)))
    questions = [make_question(bid) for bid in picks]

    memory_text = args.corpus.read_text()

    print(f"Running {args.turns} turns on {args.corpus.name} with {args.model}...")
    print("Strategy A: whole-doc cached")
    a_rows = run_whole_doc_cached(client, args.model, memory_text, questions)
    print("Strategy B: selective uncached")
    b_rows = run_selective_uncached(client, args.model, args.corpus, picks, questions)

    summary = {
        "corpus": str(args.corpus),
        "model": args.model,
        "turns": args.turns,
        "seed": args.seed,
        "whole_doc_cached": {
            "per_turn": a_rows,
            "totals": aggregate(a_rows, "whole_doc_cached"),
        },
        "selective_uncached": {
            "per_turn": b_rows,
            "totals": aggregate(b_rows, "selective_uncached"),
        },
    }

    a_total = summary["whole_doc_cached"]["totals"]["total_cost_usd"]
    b_total = summary["selective_uncached"]["totals"]["total_cost_usd"]
    summary["dollar_savings_ratio"] = round(a_total / b_total, 2) if b_total else None

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {args.out}")
    print(json.dumps({
        "whole_doc_cached_total_$": a_total,
        "selective_uncached_total_$": b_total,
        "ratio_A_over_B": summary["dollar_savings_ratio"],
        "avg_latency_A_s": summary["whole_doc_cached"]["totals"]["avg_latency_s"],
        "avg_latency_B_s": summary["selective_uncached"]["totals"]["avg_latency_s"],
    }, indent=2))


if __name__ == "__main__":
    main()
