#!/usr/bin/env python3
"""Task-level benchmark harness.

Runs SWE-bench Verified tasks through pluggable retrieval adapters and
measures:

    - patch correctness vs gold (file_overlap, function_overlap, line_jaccard)
    - off_target_files (hallucination proxy)
    - RAF: tokens_loaded / tokens_essential
    - $ via Anthropic usage
    - tool_calls

This is the v0: 1-3 adapters, single-pass per (task × strategy), no
trial averaging yet. Smoke-test with --dry-run (no API call).

The headline metric is *not* file_overlap or function_overlap alone.
The narrative is the joint distribution of (RAF, function_overlap)
across tasks — addressable retrieval is interesting precisely when it
keeps RAF low *without* hurting correctness.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from swebench_loader import load_task, list_task_ids
from raf import compute_essential, raf
from oracle import evaluate
from adapters.base import AdapterResult, RetrievalAdapter
from adapters.preload_bm25 import PreloadBM25
from adapters.grep_agent import GrepAgent
from adapters.agd_graph import AgdGraph
from agd_repo_ingest import ingest as ingest_repo

ADAPTER_NAMES = ["preload-bm25", "grep-agent", "agd-graph"]
# scripts/bench_task.py is at task_bench/scripts/bench_task.py;
# corpora live one level up at task_bench/corpora/.
TASK_CORPORA_DIR = Path(__file__).resolve().parent.parent / "corpora"


def _ensure_corpus(repo_path: Path, repo: str, base_commit: str) -> Path:
    safe = repo.replace("/", "__")
    corpus = TASK_CORPORA_DIR / f"{safe}__{base_commit[:12]}.agd"
    if not corpus.exists():
        print(f"[ingest] {repo}@{base_commit[:12]} -> {corpus.name}")
        stats = ingest_repo(repo_path, corpus)
        print(f"[ingest] {stats}")
    return corpus


def _make_adapter(name: str, repo_path: Path, repo: str, base_commit: str) -> RetrievalAdapter:
    if name == "preload-bm25":
        return PreloadBM25()
    if name == "grep-agent":
        return GrepAgent()
    if name == "agd-graph":
        corpus = _ensure_corpus(repo_path, repo, base_commit)
        return AgdGraph(corpus)
    raise SystemExit(f"unknown adapter: {name}. available: {ADAPTER_NAMES}")


def run_one(
    instance_id: str,
    adapter_name: str,
    model: str,
    token_budget: int,
    client,
    dry_run: bool,
) -> dict:
    print(f"[task] {instance_id} | adapter={adapter_name} | model={model}")
    task = load_task(instance_id)
    assert task.repo_path is not None

    print(f"[essential] computing from gold patch ({task.gold_patch.count(chr(10))} lines)")
    essential = compute_essential(task.repo_path, task.gold_patch)
    print(f"[essential] files={list(essential.file_to_lines.keys())} tokens={essential.total_tokens}")

    if dry_run:
        return {
            "instance_id": instance_id,
            "adapter": adapter_name,
            "model": model,
            "dry_run": True,
            "essential_files": list(essential.file_to_lines.keys()),
            "essential_tokens": essential.total_tokens,
        }

    adapter = _make_adapter(adapter_name, task.repo_path, task.repo, task.base_commit)
    t0 = time.perf_counter()
    res: AdapterResult = adapter.run(
        repo_root=task.repo_path,
        problem_statement=task.problem_statement,
        client=client,
        model=model,
        token_budget=token_budget,
    )
    elapsed = time.perf_counter() - t0

    oracle = evaluate(res.patch, task.gold_patch, task.repo_path)
    raf_value = raf(res.loaded_tokens, essential.total_tokens)

    return {
        "instance_id": instance_id,
        "adapter": adapter_name,
        "model": model,
        "elapsed_s": round(elapsed, 2),
        "loaded_tokens": res.loaded_tokens,
        "essential_tokens": essential.total_tokens,
        "raf": round(raf_value, 2),
        "tool_calls": res.tool_calls,
        "api": {
            "input_tokens": res.api_input_tokens,
            "output_tokens": res.api_output_tokens,
            "cache_read": res.api_cache_read,
            "cache_write": res.api_cache_write,
        },
        "oracle": {
            "file_overlap": round(oracle.file_overlap, 3),
            "function_overlap": round(oracle.function_overlap, 3),
            "line_jaccard": round(oracle.line_jaccard, 3),
            "off_target_files": oracle.off_target_files,
            "gold_files": oracle.gold_files,
            "model_files": oracle.model_files,
            "gold_functions": oracle.gold_functions,
            "model_functions": oracle.model_functions,
        },
        "adapter_notes": res.notes,
        "patch_first_line": res.patch.splitlines()[0] if res.patch else "",
        "patch_lines": res.patch.count("\n"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", help="run a single instance")
    ap.add_argument("--n", type=int, default=5, help="how many instances to sample")
    ap.add_argument("--repo-filter", default=None, help="restrict to one SWE-bench repo, e.g. django/django")
    ap.add_argument("--adapter", action="append", default=None,
                    help="adapter name (repeatable). Default: all available")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--token-budget", type=int, default=150_000)
    ap.add_argument("--out", type=Path, default=Path("results/run.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="skip API calls; just compute essential context for each task")
    args = ap.parse_args()

    adapters = args.adapter or list(ADAPTER_NAMES)
    for a in adapters:
        if a not in ADAPTER_NAMES:
            raise SystemExit(f"unknown adapter: {a}. available: {ADAPTER_NAMES}")

    if args.instance_id:
        ids = [args.instance_id]
    else:
        ids = list_task_ids(n=args.n, repo_filter=args.repo_filter)
        print(f"[sample] {len(ids)} instance_ids: {ids[:5]}{'…' if len(ids) > 5 else ''}")

    client = None
    if not args.dry_run:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY not set (or pass --dry-run)")
        import anthropic
        client = anthropic.Anthropic()

    results = []
    for iid in ids:
        for aname in adapters:
            try:
                row = run_one(iid, aname, args.model, args.token_budget, client, args.dry_run)
            except Exception as e:
                row = {"instance_id": iid, "adapter": aname, "error": f"{type(e).__name__}: {e}"}
                print(f"[error] {iid} {aname}: {e}")
            results.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "model": args.model,
        "token_budget": args.token_budget,
        "adapters": adapters,
        "n_instances": len(ids),
        "results": results,
    }, indent=2))
    print(f"\nwrote {args.out} ({len(results)} rows)")


if __name__ == "__main__":
    main()
