#!/usr/bin/env python3
"""Benchmark agd-memory selective retrieval vs whole-doc loading.

Token accounting only — no API calls. Uses tiktoken cl100k_base as a
proxy for the Claude tokenizer. Absolute counts will differ from
Anthropic's exact tokenizer but ratios are stable.

Scenarios
---------
S0 realism: synthetic-vs-real ratio sanity check.
S1 single-agent baseline: whole vs TOC vs TOC+1 vs TOC+3 across sizes.
S2 N parallel agents on shared memory (naive vs selective).
S3 multi-turn session, 20 turns, each needing different context.
S4 cross-context: filter by --kind for 3 task types on the same corpus.
S5 format comparison: same content as AGD vs Markdown vs XML.

Plus a latency table of the agd CLI.

Outputs JSON to results/summary.json.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import time
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("cl100k_base")


def tok(text: str) -> int:
    return len(ENC.encode(text))


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return r.stdout


def whole_doc(path: Path) -> int:
    return tok(path.read_text())


def toc(path: Path, kind: str | None = None) -> int:
    cmd = ["agd", "ids", str(path)]
    if kind:
        cmd += ["--kind", kind]
    return tok(run(cmd))


def get_block(path: Path, ids: list[str]) -> int:
    out = "".join(run(["agd", "get", str(path), bid]) for bid in ids)
    return tok(out)


def get_with_backlinks(path: Path, anchor: str) -> int:
    return tok(run(["agd", "get", str(path), anchor, "--with-backlinks"]))


def list_ids(path: Path, kind: str | None = None) -> list[str]:
    cmd = ["agd", "ids", str(path)]
    if kind:
        cmd += ["--kind", kind]
    out = run(cmd)
    return [
        f"#{line.split(chr(9), 1)[0].strip()}"
        for line in out.splitlines()
        if line.strip() and not line.startswith(("meta", "root", "h-"))
    ]


# --------------------------------------------------------------------------
# S1 — single-agent baseline across sizes
# --------------------------------------------------------------------------
def s1_single_agent(corpora: dict[str, Path], rng: random.Random) -> list[dict]:
    rows = []
    for label, path in corpora.items():
        all_ids = list_ids(path)
        pick1 = rng.sample(all_ids, 1)
        pick3 = rng.sample(all_ids, min(3, len(all_ids)))
        whole = whole_doc(path)
        toc_only = toc(path)
        toc_plus_1 = toc_only + get_block(path, pick1)
        toc_plus_3 = toc_only + get_block(path, pick3)
        rows.append({
            "size": label,
            "blocks": len(all_ids),
            "whole_doc": whole,
            "toc_only": toc_only,
            "toc_plus_1": toc_plus_1,
            "toc_plus_3": toc_plus_3,
            "savings_toc": round(whole / toc_only, 1),
            "savings_toc_plus_1": round(whole / toc_plus_1, 1),
            "savings_toc_plus_3": round(whole / toc_plus_3, 1),
        })
    return rows


# --------------------------------------------------------------------------
# S2 — N parallel agents, fixed corpus
# --------------------------------------------------------------------------
def s2_parallel_agents(path: Path, ns: list[int], rng: random.Random) -> list[dict]:
    all_ids = list_ids(path)
    whole = whole_doc(path)
    toc_only = toc(path)
    rows = []
    for n in ns:
        # Each agent fetches its own random 1-3 blocks.
        per_agent_picks = [rng.sample(all_ids, rng.randint(1, 3)) for _ in range(n)]

        naive = n * whole
        # Each agent independently loads TOC + its picks (no sharing).
        independent = sum(toc_only + get_block(path, pk) for pk in per_agent_picks)
        # Parent loads TOC once, dispatches only block slices to children.
        shared_toc = toc_only + sum(get_block(path, pk) for pk in per_agent_picks)
        rows.append({
            "n_agents": n,
            "naive_each_loads_whole": naive,
            "independent_toc_plus_picks": independent,
            "shared_toc_plus_picks": shared_toc,
            "savings_independent": round(naive / independent, 1),
            "savings_shared": round(naive / shared_toc, 1),
        })
    return rows


# --------------------------------------------------------------------------
# S3 — multi-turn session
# --------------------------------------------------------------------------
def s3_multi_turn(path: Path, n_turns: int, rng: random.Random) -> list[dict]:
    all_ids = list_ids(path)
    whole = whole_doc(path)
    toc_only = toc(path)

    # Naive: load whole doc once, kept in context for all turns.
    # Selective: TOC once + per-turn block fetches (1-2 blocks per turn).
    selective_total = toc_only
    per_turn_costs = []
    for _ in range(n_turns):
        picks = rng.sample(all_ids, rng.randint(1, 2))
        c = get_block(path, picks)
        per_turn_costs.append(c)
        selective_total += c

    return [{
        "turns": n_turns,
        "whole_doc_kept": whole,
        "selective_cumulative": selective_total,
        "savings": round(whole / selective_total, 1),
        "per_turn_avg": round(sum(per_turn_costs) / n_turns, 1),
    }]


# --------------------------------------------------------------------------
# S4 — cross-context filtering by kind
# --------------------------------------------------------------------------
CONTEXTS = {
    "blog-write": ["x-user", "x-feedback"],
    "code-review": ["x-feedback", "x-reference"],
    "project-status": ["x-project"],
}


def s4_cross_context(path: Path) -> list[dict]:
    whole = whole_doc(path)
    rows = []
    for ctx, kinds in CONTEXTS.items():
        # Cumulative TOC across the requested kinds.
        scoped = sum(toc(path, k) for k in kinds)
        rows.append({
            "context": ctx,
            "kinds": ",".join(kinds),
            "whole_doc": whole,
            "scoped_toc": scoped,
            "savings": round(whole / scoped, 1) if scoped else None,
        })
    return rows


# --------------------------------------------------------------------------
# S0 — synthetic-vs-real realism check
# --------------------------------------------------------------------------
def s0_realism(synthetic: Path, real: Path | None) -> dict:
    syn_whole = whole_doc(synthetic)
    syn_toc = toc(synthetic)
    syn = {
        "label": "synthetic mem-50",
        "blocks": len(list_ids(synthetic)),
        "whole_doc": syn_whole,
        "toc": syn_toc,
        "ratio": round(syn_whole / syn_toc, 1),
    }
    if real and real.exists():
        rl_whole = whole_doc(real)
        rl_toc = toc(real)
        rl = {
            "label": "real memory.agd",
            "blocks": len(list_ids(real)),
            "whole_doc": rl_whole,
            "toc": rl_toc,
            "ratio": round(rl_whole / rl_toc, 1),
        }
    else:
        rl = {"label": "real memory.agd", "note": "not provided, skipped"}
    return {"synthetic": syn, "real": rl}


# --------------------------------------------------------------------------
# S5 — format comparison (AGD vs Markdown vs XML on same content)
# --------------------------------------------------------------------------
BLOCK_RE = re.compile(
    r"@(x-\w+)\s+desc=\"([^\"]+)\"(?:\s+refs=\"([^\"]+)\")?\s*\[#([^\]]+)\]\n~~~\n(.*?)\n~~~",
    re.DOTALL,
)


def parse_agd(text: str) -> list[dict]:
    return [
        {"kind": k, "desc": d, "refs": r or "", "id": i, "body": b}
        for k, d, r, i, b in BLOCK_RE.findall(text)
    ]


def to_markdown(blocks: list[dict]) -> str:
    """Plain markdown: section per block, no IDs (loses addressability)."""
    parts = ["# Project memory\n"]
    for b in blocks:
        parts.append(f"## {b['desc']}\n\n{b['body']}\n")
    return "\n".join(parts)


def to_xml(blocks: list[dict]) -> str:
    """XML with addressable id attributes."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<memory>"]
    for b in blocks:
        refs_attr = f' refs="{b["refs"]}"' if b["refs"] else ""
        parts.append(
            f'  <block kind="{b["kind"]}" id="{b["id"]}"{refs_attr}>'
            f"<desc>{b['desc']}</desc>"
            f"<body>{b['body']}</body>"
            f"</block>"
        )
    parts.append("</memory>")
    return "\n".join(parts)


def xml_id_list(blocks: list[dict]) -> str:
    """The XML equivalent of a TOC: id+desc per line."""
    return "\n".join(f"{b['id']}\t{b['desc']}" for b in blocks)


def xml_one_block(blocks: list[dict], target_id: str) -> str:
    for b in blocks:
        if b["id"] == target_id:
            refs_attr = f' refs="{b["refs"]}"' if b["refs"] else ""
            return (
                f'<block kind="{b["kind"]}" id="{b["id"]}"{refs_attr}>'
                f"<desc>{b['desc']}</desc>"
                f"<body>{b['body']}</body>"
                f"</block>"
            )
    raise KeyError(target_id)


def s5_format_comparison(path: Path, rng: random.Random) -> list[dict]:
    text = path.read_text()
    blocks = parse_agd(text)
    md = to_markdown(blocks)
    xml = to_xml(blocks)

    target = rng.choice(blocks)
    target_id = target["id"]

    # AGD selective: TOC + agd get one block (already a CLI op)
    agd_whole = tok(text)
    agd_toc = toc(path)
    agd_one = get_block(path, [f"#{target_id}"])

    # XML selective: id-list (TOC analog) + extracted block
    xml_whole = tok(xml)
    xml_idlist = tok(xml_id_list(blocks))
    xml_one = tok(xml_one_block(blocks, target_id))

    # Markdown: not addressable. Best case = whole doc.
    md_whole = tok(md)

    return [
        {
            "format": "AGD",
            "whole_doc": agd_whole,
            "toc_or_idlist": agd_toc,
            "selective_fetch": agd_toc + agd_one,
            "savings_vs_whole": round(agd_whole / (agd_toc + agd_one), 1),
            "addressable": True,
        },
        {
            "format": "XML",
            "whole_doc": xml_whole,
            "toc_or_idlist": xml_idlist,
            "selective_fetch": xml_idlist + xml_one,
            "savings_vs_whole": round(xml_whole / (xml_idlist + xml_one), 1),
            "addressable": True,
        },
        {
            "format": "Markdown",
            "whole_doc": md_whole,
            "toc_or_idlist": None,
            "selective_fetch": md_whole,
            "savings_vs_whole": 1.0,
            "addressable": False,
        },
    ]


# --------------------------------------------------------------------------
# S6 — backlink explosion
# --------------------------------------------------------------------------
def s6_backlink_explosion(corpora_dir: Path) -> list[dict]:
    rows = []
    anchor_id = "#project-anchor-001"
    for fanin in [5, 50, 200]:
        path = corpora_dir / f"anchor-fanin-{fanin:03d}.agd"
        if not path.exists():
            rows.append({"fanin": fanin, "note": f"missing corpus {path.name}"})
            continue
        whole = whole_doc(path)
        toc_only = toc(path)
        anchor_only = get_block(path, [anchor_id])
        with_backlinks = get_with_backlinks(path, anchor_id)
        rows.append({
            "fanin": fanin,
            "whole_doc": whole,
            "anchor_only": anchor_only,
            "with_backlinks": with_backlinks,
            "ratio_vs_anchor": round(with_backlinks / anchor_only, 1),
            "fraction_of_whole": round(with_backlinks / whole, 2),
        })
    return rows


# --------------------------------------------------------------------------
# Latency table
# --------------------------------------------------------------------------
def latency_table(path: Path, n: int = 20) -> list[dict]:
    all_ids = list_ids(path)
    pick = all_ids[len(all_ids) // 2]
    cmds = [
        ("agd ids", ["agd", "ids", str(path)]),
        ("agd ids --kind", ["agd", "ids", str(path), "--kind", "x-feedback"]),
        ("agd get 1 block", ["agd", "get", str(path), pick]),
        ("cat (I/O baseline)", ["cat", str(path)]),
    ]
    rows = []
    for label, cmd in cmds:
        ts = []
        for _ in range(n):
            s = time.perf_counter()
            subprocess.run(cmd, capture_output=True, check=True)
            ts.append((time.perf_counter() - s) * 1000)
        ts.sort()
        rows.append({"op": label, "median_ms": round(ts[n // 2], 1)})
    return rows


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpora-dir", type=Path, default=Path("corpora"))
    ap.add_argument("--out-dir", type=Path, default=Path("results"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--real-memory",
        type=Path,
        default=None,
        help="Path to a real memory.agd for the S0 realism check (optional)",
    )
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    corpora = {
        "10": args.corpora_dir / "mem-10.agd",
        "50": args.corpora_dir / "mem-50.agd",
        "200": args.corpora_dir / "mem-200.agd",
        "1000": args.corpora_dir / "mem-1000.agd",
    }
    for p in corpora.values():
        if not p.exists():
            raise SystemExit(f"missing corpus: {p}")

    rng = random.Random(args.seed)

    print("S0 synthetic-vs-real realism check...")
    s0 = s0_realism(corpora["50"], args.real_memory)
    print("S1 single-agent baseline...")
    s1 = s1_single_agent(corpora, rng)
    print("S2 N parallel agents (corpus size 200)...")
    s2 = s2_parallel_agents(corpora["200"], [1, 3, 5, 10, 20], rng)
    print("S3 multi-turn session, 20 turns (corpus size 200)...")
    s3 = s3_multi_turn(corpora["200"], 20, rng)
    print("S4 cross-context filtering (corpus size 200)...")
    s4 = s4_cross_context(corpora["200"])
    print("S5 format comparison AGD/Markdown/XML (corpus size 200)...")
    s5 = s5_format_comparison(corpora["200"], rng)
    print("S6 backlink explosion (fan-in 5/50/200)...")
    s6 = s6_backlink_explosion(args.corpora_dir)
    print("Latency table (corpus size 1000, n=20)...")
    lat = latency_table(corpora["1000"])

    summary = {
        "seed": args.seed,
        "s0": s0,
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "s4": s4,
        "s5": s5,
        "s6": s6,
        "latency_ms": lat,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {args.out_dir / 'summary.json'}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
