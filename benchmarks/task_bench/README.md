# task-level benchmark

Sibling of the token-level bench in `../`. Where that one answers
**"how many tokens does selective retrieval ship vs whole-doc?"**,
this one answers:

> **"On a real coding task, does addressable retrieval keep RAF
> low *without* hurting patch correctness?"**

The task source is [SWE-bench Verified][swev]: 500 hand-validated
bug-fix instances with gold patches and pytest oracles. v0 uses the
gold-patch as a patch-similarity oracle (no Docker); the test-execution
oracle is deferred to v1.

[swev]: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified

## Layout

```
task_bench/
├── REPORT_v0.md            # full internal write-up of the v0 results
├── requirements.txt        # datasets, anthropic, tiktoken
├── scripts/
│   ├── swebench_loader.py  # dataset + git clone cache (~/.cache/agd-memory-bench/repos)
│   ├── raf.py              # essential context (from gold patch) + RAF computation
│   ├── oracle.py           # patch-similarity (file/function/line overlap, off-target)
│   ├── agd_repo_ingest.py  # Python AST → AGD corpus, intra-module call edges
│   ├── bench_task.py       # orchestrator (per-task × per-adapter)
│   ├── summarize_task.py   # JSON + Markdown aggregation
│   └── adapters/
│       ├── base.py
│       ├── preload_bm25.py # BM25-rank repo files, fill budget, single-shot
│       ├── grep_agent.py   # ripgrep + read_file + submit_patch
│       └── agd_graph.py    # agd_toc(file=) + agd_get + agd_search + read_window
├── corpora/                # cached AGD corpora keyed by <repo>__<sha[:12]>.agd
└── results/                # per-run JSON + Markdown summaries
```

## Run

```sh
cd task_bench
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

# all adapters on 8 psf/requests tasks (the v0 baseline)
python3 scripts/bench_task.py \
  --n 8 --repo-filter psf/requests \
  --token-budget 80000 \
  --out results/my_run.json

# aggregate to JSON + Markdown
python3 scripts/summarize_task.py results/my_run.json

# single instance, single adapter (debugging)
python3 scripts/bench_task.py \
  --instance-id psf__requests-1921 \
  --adapter agd-graph \
  --out results/debug.json

# essential-context only, no API calls
python3 scripts/bench_task.py --n 5 --dry-run
```

Default model: `claude-haiku-4-5`. Default token budget: 150 KB. Cost
per `psf/requests`-sized run (3 adapters × 8 tasks): ~$2.50 at Haiku.

## Results so far (from REPORT_v0.md §4)

8 SWE-bench Verified `psf/requests` tasks, Haiku 4.5:

| adapter | RAF median | function_overlap | line_jaccard | $ total |
|---|---:|---:|---:|---:|
| `preload-bm25` | 128 | 0.44 | 0.01 | $0.83 |
| `grep-agent`   | 8.6 | 0.50 | 0.34 | $0.79 |
| `agd-graph` v2 | **7.4** | **0.69** | **0.43** | $0.81 |

The full discussion — methodology, the v1 → v2 iteration story (the
most informative datum in the v0), honest caveats, and what's worth
running next — is in [`REPORT_v0.md`](./REPORT_v0.md).

## Status

v0. 1 repo, 1 model, 8 tasks, no trial averaging, weak oracle. Results
are *directional*. To tighten: add `pytest-dev/pytest` and
`sphinx-doc/sphinx`, re-run on Sonnet, plumb the SWE-bench pytest
oracle.
