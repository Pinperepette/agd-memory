#!/usr/bin/env python3
"""SWE-bench Verified loader with on-disk repo caching.

Pulls task metadata from the HuggingFace dataset
`princeton-nlp/SWE-bench_Verified` (split=test) and clones the relevant
repo at the task's `base_commit` into a local cache. Subsequent
requests for tasks on the same repo reuse the clone and just
`git checkout` the right SHA.

A SWE-bench task carries:
- instance_id     : unique key, e.g. "django__django-12345"
- repo            : "owner/name", e.g. "django/django"
- base_commit     : SHA the patch is applied against
- problem_statement
- patch           : gold patch (unified diff). Oracle truth.
- test_patch      : gold test changes. Oracle truth.
- FAIL_TO_PASS    : tests that should fail before the patch and pass after.
- PASS_TO_PASS    : tests that should pass before AND after.

We keep test_patch and FAIL_TO_PASS for later (Docker-based oracle);
v0 uses only `patch` for the patch-similarity oracle.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

DEFAULT_CACHE = Path.home() / ".cache" / "agd-memory-bench" / "repos"
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"


@dataclass
class Task:
    instance_id: str
    repo: str               # "django/django"
    base_commit: str
    problem_statement: str
    gold_patch: str         # unified diff
    test_patch: str         # gold test changes
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    repo_path: Path | None = None  # set after checkout

    def to_meta(self) -> dict[str, Any]:
        d = asdict(self)
        d["repo_path"] = str(self.repo_path) if self.repo_path else None
        return d


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.PIPE).decode()


def _ensure_clone(repo: str, cache_dir: Path) -> Path:
    """Clone owner/name into cache_dir/owner__name if missing."""
    safe = repo.replace("/", "__")
    target = cache_dir / safe
    if target.exists():
        return target
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    print(f"[clone] {url} -> {target}")
    _run(["git", "clone", "--quiet", url, str(target)])
    return target


def _checkout(repo_path: Path, sha: str) -> None:
    """Detached checkout. Resets any leftover state from a prior task."""
    _run(["git", "reset", "--hard", "HEAD"], cwd=repo_path)
    _run(["git", "clean", "-fdx", "-e", ".bench_keep"], cwd=repo_path)
    try:
        _run(["git", "checkout", "--quiet", "--detach", sha], cwd=repo_path)
    except subprocess.CalledProcessError:
        # Probably need to fetch the commit; SWE-bench SHAs are sometimes
        # not on default branch.
        _run(["git", "fetch", "--quiet", "origin", sha], cwd=repo_path)
        _run(["git", "checkout", "--quiet", "--detach", sha], cwd=repo_path)


def load_task(
    instance_id: str,
    cache_dir: Path = DEFAULT_CACHE,
    dataset: str = DEFAULT_DATASET,
) -> Task:
    """Resolve one SWE-bench Verified task by instance_id and check out its base_commit."""
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise SystemExit("missing dep: pip install datasets") from e

    ds = load_dataset(dataset, split="test")
    rows = ds.filter(lambda r: r["instance_id"] == instance_id)
    if len(rows) == 0:
        raise SystemExit(f"instance_id not found in {dataset}: {instance_id}")
    r = rows[0]

    repo_path = _ensure_clone(r["repo"], cache_dir)
    _checkout(repo_path, r["base_commit"])

    return Task(
        instance_id=r["instance_id"],
        repo=r["repo"],
        base_commit=r["base_commit"],
        problem_statement=r["problem_statement"],
        gold_patch=r["patch"],
        test_patch=r["test_patch"],
        fail_to_pass=_parse_test_list(r.get("FAIL_TO_PASS")),
        pass_to_pass=_parse_test_list(r.get("PASS_TO_PASS")),
        repo_path=repo_path,
    )


def _parse_test_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return list(v)
    if isinstance(v, str):
        try:
            return list(json.loads(v))
        except json.JSONDecodeError:
            return [v]
    return []


def list_task_ids(
    n: int = 20,
    seed: int = 42,
    dataset: str = DEFAULT_DATASET,
    repo_filter: str | None = None,
) -> list[str]:
    """Return a deterministic sample of instance_ids, optionally restricted to one repo."""
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise SystemExit("missing dep: pip install datasets") from e
    import random

    ds = load_dataset(dataset, split="test")
    if repo_filter:
        ds = ds.filter(lambda r: r["repo"] == repo_filter)
    ids = sorted([r["instance_id"] for r in ds])
    rng = random.Random(seed)
    rng.shuffle(ids)
    return ids[:n]


if __name__ == "__main__":
    # Smoke test: list 5 ids, load the first.
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-id", default=None)
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    if not args.instance_id:
        ids = list_task_ids(n=5, repo_filter=args.repo)
        print(json.dumps({"sample_instance_ids": ids}, indent=2))
        if not ids:
            raise SystemExit(0)
        args.instance_id = ids[0]
        print(f"[smoke] loading {args.instance_id}")

    t = load_task(args.instance_id)
    print(json.dumps({
        "instance_id": t.instance_id,
        "repo": t.repo,
        "base_commit": t.base_commit[:12],
        "problem_statement_first_line": t.problem_statement.splitlines()[0][:120] if t.problem_statement else "",
        "gold_patch_lines": t.gold_patch.count("\n"),
        "fail_to_pass_count": len(t.fail_to_pass),
        "pass_to_pass_count": len(t.pass_to_pass),
        "repo_path": str(t.repo_path),
    }, indent=2))
