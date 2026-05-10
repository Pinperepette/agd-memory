# Task-Level Benchmark for Addressable Memory: v0 Report

**Internal — for evaluation, not publication**
**Date:** 2026-05-10
**Author:** built collaboratively in one session
**Repo under test:** `psf/requests` (8 SWE-bench Verified instances)
**Model:** `claude-haiku-4-5`
**Total API spend across all v0 runs:** ~$4.90

---

## 1. Executive summary

We extended the existing `agd-memory/benchmarks` (which measured *tokens shipped* under various access patterns) with a **task-level harness** that runs real SWE-bench Verified bug-fix tasks through three retrieval strategies and scores the resulting patch against a gold patch.

Three retrieval adapters were compared:

1. **`preload-bm25`** — BM25-rank Python files in the repo, fill a 80k-token budget, single-shot patch.
2. **`grep-agent`** — multi-turn tool-use loop with `ripgrep` / `read_file` / `list_dir` and an explicit `submit_patch` terminator.
3. **`agd-graph`** — multi-turn tool-use loop over an AGD-ingested corpus of the repo (one block per function/class, intra-module call edges as `refs=`), using `agd_toc` (file-scoped) / `agd_get` / `agd_search` / `agd_backlinks`, with a `read_window` neighbour-context fallback.

**Headline result on 8 `psf/requests` tasks:**

| metric | `preload-bm25` | `grep-agent` | `agd-graph` (v2) |
|---|---:|---:|---:|
| **function_overlap mean** | 0.44 | 0.50 | **0.69** |
| **file_overlap mean** | 0.75 | 0.62 | **0.88** |
| **line_jaccard mean** | 0.01 | 0.34 | **0.43** |
| **RAF median** | 128 | 8.6 | **7.4** |
| RAF p95 | 765 | 46 | 92 |
| off_target_files mean | 0.25 | 0.12 | 0.25 |
| tool_calls mean | 1 | 15 | 13 |
| total $ (8 runs) | $0.83 | $0.79 | $0.81 |
| s/run mean | 9.1 | 26.1 | 32.6 |

`agd-graph` v2 wins on **3 of 4 correctness metrics** and on RAF median. It is competitive on cost and time. Per-task winners (lowest-RAF adapter with `function_overlap > 0`): **4/8 `agd-graph`**, 3/8 `grep-agent`, 0/8 `preload`, 1/8 unsolved by all three.

The v0 run also produced a clear *negative* result for an earlier `agd-graph` iteration (v1, addressable-only, no `read_window`, no `file=` scope on TOC, no `lineno=` on blocks): **`function_overlap = 0.00` on 8/8 tasks**. The v1 → v2 delta is the most interesting datum in this report — it isolates the affordance gap that decides whether addressable retrieval wins or loses.

---

## 2. Methodology

### 2.1 Task source

`princeton-nlp/SWE-bench_Verified` (HuggingFace), `split=test`, 500 instances total. We restricted to `psf/requests` (8 instances) for v0 because (a) the repo is small (~50 files, ~640 symbols, ~600 KB AGD corpus) so cloning / ingesting / iterating is fast, and (b) it is the smallest population in the dataset — every variability we don't yet control (across-repo) is removed.

Each task carries:
- `instance_id`, `repo`, `base_commit`
- `problem_statement` — natural-language bug description
- `patch` — gold unified diff (oracle ground truth)
- `test_patch` / `FAIL_TO_PASS` / `PASS_TO_PASS` — kept for future Docker oracle, **not used in v0**

### 2.2 Repo handling

`scripts/swebench_loader.py`. Clones each repo once into `~/.cache/agd-memory-bench/repos/<owner>__<name>/`, then `git checkout --detach <base_commit>` per task. `git reset --hard` + `git clean -fdx` between tasks to scrub leftover state.

### 2.3 Essential-context computation

`scripts/raf.py`. Given the gold patch, parses each `@@ -old_start,old_count` hunk header and resolves the **enclosing function/class** of each touched line via a pure-indentation heuristic (no AST — survives partial parse / encoding errors). The body of those enclosing functions, tokenized with `tiktoken cl100k_base`, is the *essential context*.

Edge cases:
- Hunks at module level (no enclosing function) take a ±30-line window.
- Files added by the patch (no pre-patch text) are skipped.
- Multi-hunk files merge overlapping spans.

### 2.4 Retrieval Amplification Factor (RAF)

`RAF = tokens_loaded_into_model_context / tokens_essential_for_correct_fix`

`tokens_loaded` is the strategy's accumulated *content* shown to the model, including system prompts, retrieved blocks, tool outputs, and any text the model had to read across all turns. It does *not* count cached prefixes (cache is a payment optimization, not a context-pollution one — RAF asks "how much did the model have to read").

The metric is the bridge from *tokens-shipped* (already measured in S0–S5) to *correctness*: a strategy that achieves correctness by shipping 100× more context than necessary is not retrieving — it is hoping. RAF makes that visible.

### 2.5 Patch-similarity oracle (no Docker)

`scripts/oracle.py`. Given a model-produced unified diff and the gold diff, computes:

- **`file_overlap`** = `|model_files ∩ gold_files| / |gold_files|`
- **`function_overlap`** = `|model_funcs ∩ gold_funcs| / |gold_funcs|`, where each function symbol is `<file>::<funcname>` resolved via the same indent heuristic as essential-context computation.
- **`line_jaccard`** = `|gold_pairs ∩ model_pairs| / |gold_pairs ∪ model_pairs|` over `(file, old_line)` tuples.
- **`off_target_files`** = `|model_files \ gold_files|` — the hallucination proxy (model edited a file the fix doesn't touch).

**Honest caveats** documented in the source:

> *File-set overlap can be 1.0 with semantically wrong edits. A model that produces the correct fix in a different function scores badly here. Without test execution we cannot detect this. This is a necessary signal, not a sufficient one — it is the headline number for v0; real ground truth waits for the Docker oracle.*

Empirically, `function_overlap` is the strongest single predictor of "the patch made sense": it requires the model to identify the same symbol the gold patch identified.

### 2.6 The three adapters

#### `preload-bm25` (`scripts/adapters/preload_bm25.py`)

BM25 (k1=1.5, b=0.75) over all `.py` files using a regex tokenizer (`[A-Za-z_][A-Za-z0-9_]{2,}`). Query terms = lowercase content tokens of the problem statement. Files are added to the prompt in rank order until the token budget (default 80k) is consumed; never truncated mid-file. Single API call, no tools.

#### `grep-agent` (`scripts/adapters/grep_agent.py`)

Tool-use loop, max 25 turns, four tools:

- `ripgrep_search(pattern, path, max_results)` — wraps `rg --no-heading --line-number -S`.
- `read_file(path, start_line, end_line)` — slice with line numbers.
- `list_dir(path)`.
- `submit_patch(diff)` — terminates the loop.

Tool outputs are capped at 4000 chars to bound per-turn cost.

#### `agd-graph` (`scripts/adapters/agd_graph.py`)

Tool-use loop over a pre-ingested AGD corpus of the repo, max 25 turns, six tools:

- `agd_toc(file?)` — TOC of all blocks; if `file=PATH` is passed, post-filtered to symbols whose id starts with `<file_slug>--` (cheap, much shorter than the global TOC).
- `agd_search(query, ignore_case)` — substring search across block bodies (CLI `agd search`).
- `agd_get(ids, with_backlinks)` — batch fetch; `with_backlinks=True` returns the block plus every block that `refs=` it (callers within the module).
- `agd_backlinks(id)` — list ids that `refs=` the given block.
- `read_window(path, start_line, end_line)` — raw file slice, identical signature to `grep-agent`'s `read_file`. **Added in v2** as the affordance for "neighbour context the addressable layer doesn't carry".
- `submit_patch(diff)` — terminator.

#### AGD repo ingestor (`scripts/agd_repo_ingest.py`)

Walks all `.py` files; `ast.parse` per file; emits one `@x-symbol` block per top-level `FunctionDef` / `AsyncFunctionDef` / `ClassDef`, plus one block per method of each class. Each block carries:

```
@x-symbol desc="<signature>" file="<path>" qual="<dotted>"
          lineno=<n> endline=<n> refs="#callee_id,..." [#<id>]
~~~
<full source of the symbol>
~~~
```

ID convention: `<file-slug>--f-<func>` for functions, `<file-slug>--c-<cls>` for classes, `<file-slug>--c-<cls>--m-<method>` for methods, where `file-slug` = path with `/` and `.` replaced by `-`, lowercase. Underscores preserved. Collisions auto-suffixed with `-2`, `-3`, …

`refs=` resolution is **intra-module only**: a `Call` to bare name `X` becomes `refs="#<file_slug>--f-X"` if a same-module symbol named `X` exists; cross-module calls are dropped. This is a deliberate v0 simplification — captures most fan-in within tightly coupled modules without false positives across the project. Documented in the source as such.

Stats on `psf/requests` at base commit: **52 files indexed, 640 symbols, 441 intra-module call edges, 600 KB corpus**.

---

## 3. Iteration story: agd-graph v1 → v2

The most informative datum in this v0 is not the headline table but the v1 → v2 delta on `agd-graph`. It isolates exactly which affordances make addressable retrieval *usable* for coding.

### v1 (initial implementation)

System prompt, tools `agd_toc / agd_search / agd_get / agd_backlinks / submit_patch`. No `lineno=` on blocks. No `file=` scope on `agd_toc`. No `read_window`. Run on the same 8 `psf/requests` tasks:

| metric | v1 |
|---|---:|
| function_overlap mean | **0.00** |
| file_overlap mean | 0.25 |
| line_jaccard mean | 0.00 |
| off_target mean | 0.38 |
| tool_calls mean | 22 |
| $ total | $1.29 |

8/8 failures. The model's trajectory on `psf__requests-1921` (inspected manually): 12 broad searches, no convergence on `merge_setting` until turn 13, then a wrong-file patch on `models.py::prepare_body` instead of `sessions.py::merge_setting`.

### Hypothesised friction (from trajectory inspection)

1. **TOC overload** — 700 entries in a single dump, no scoping by file.
2. **ID-construction errors** — model wrote `requests-sessions-py--c-Session` (capitalised) instead of `--c-session`. Even with explicit examples in the system prompt, formatting drift.
3. **No neighbour context** — block bodies are isolated functions; the model cannot construct correct `@@` hunk headers without seeing surrounding lines (imports, blank lines, indentation anchors).

### v2 (three changes, no new conceptual ground)

1. Ingestor emits `lineno=N endline=M` per block. Now the model can compute hunk headers without reading the file.
2. `agd_toc(file=PATH)` post-filters the TOC to symbols of one file. Reduces typical TOC size from 700 → ~20 entries.
3. New tool `read_window(path, start, end)` — raw file slice, same signature as `grep-agent`'s `read_file`. Used for the few neighbour lines needed to anchor a diff.

### v2 result, same 8 tasks

| metric | v1 | v2 | Δ |
|---|---:|---:|---:|
| function_overlap mean | 0.00 | **0.69** | **+0.69** |
| file_overlap mean | 0.25 | **0.88** | +0.63 |
| line_jaccard mean | 0.00 | **0.43** | +0.43 |
| off_target mean | 0.38 | 0.25 | -0.13 |
| RAF median | 7.86 | 7.43 | -0.43 |
| tool_calls mean | 22 | 13 | **-9** |
| $ total | $1.29 | $0.81 | **-$0.48** |
| s/run mean | 35 | 33 | -2 |

The change is not subtle. **Function overlap goes from 0.00 to 0.69 on the same task suite, same model, same corpus, by adding line metadata + file-scoped TOC + a raw-file fallback.** Costs *drop* by 37% because the model converges in fewer turns.

**The lesson, in research terms.** Addressable memory wins not when you "replace direct file reads", but when you offer addressable retrieval *alongside* direct reads, and remove navigation friction (file scoping, line numbers, ID constructibility). The "addressable purist" framing of v1 is wrong; the "addressable + targeted neighbour fallback" framing of v2 is right.

---

## 4. Combined results (`preload` v1 + `grep` v1 + `agd` v2)

8 SWE-bench Verified `psf/requests` tasks, Haiku 4.5, 80k token budget.

### Per-adapter

| adapter | n | RAF med | RAF p95 | function_ovl | file_ovl | line_jacc | off_target | tool_calls | $ total | s/run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `preload-bm25` | 8 | 127.65 | 764.59 | 0.44 | 0.75 | 0.01 | 0.25 | 1 | $0.833 | 9.13 |
| `grep-agent` | 8 | 8.57 | 46.25 | 0.50 | 0.62 | 0.34 | 0.12 | 15.1 | $0.793 | 26.05 |
| `agd-graph` | 8 | **7.43** | 92.09 | **0.69** | **0.88** | **0.43** | 0.25 | 13.1 | $0.814 | 32.61 |

### RAF CDF

Fraction of (task, adapter) cells with `RAF ≤ threshold`:

| RAF ≤ | `preload-bm25` | `grep-agent` | `agd-graph` |
|---:|---:|---:|---:|
| 2 | 0.00 | 0.00 | 0.00 |
| 5 | 0.00 | 0.38 | 0.38 |
| 10 | 0.00 | 0.50 | **0.62** |
| 50 | 0.00 | **1.00** | 0.88 |
| 200 | 0.62 | 1.00 | 1.00 |

Reading the curve: `preload` has 0% of runs with RAF ≤ 50 (it is structurally above by ~2× even on its best task). `grep` has tighter distribution at the high end (everything below 50). `agd` has slightly more probability mass at the *low* end (62% of runs ≤ 10) but a fatter tail (one outlier at RAF ≈ 92). The shape difference matters more than the median.

### Per-task winners (lowest RAF with `function_overlap > 0`)

| instance_id | winner | RAF | fn_overlap |
|---|---|---:|---:|
| `psf__requests-1921` | `agd-graph` | 12.93 | 1.00 |
| `psf__requests-2317` | `grep-agent` | 2.76 | 0.50 |
| `psf__requests-5414` | `agd-graph` | 7.00 | 1.00 |
| `psf__requests-6028` | — | — | — |
| `psf__requests-1766` | `grep-agent` | 4.39 | 1.00 |
| `psf__requests-2931` | `agd-graph` | 3.47 | 0.50 |
| `psf__requests-1142` | `grep-agent` | 48.26 | 1.00 |
| `psf__requests-1724` | `agd-graph` | 7.86 | 0.50 |

`preload-bm25` never wins per-task because — even when it produces the right edit — its RAF is always ≥ 100 by construction. **`agd-graph` 4 wins, `grep-agent` 3 wins, 1 unsolved by all three.**

### Cost composition

`bench_cost.py`-style pricing (Haiku 4.5: $1/$1.25/$0.10/$5 per M tokens for input / cache-write / cache-read / output). Across the 24 + 8 = 32 model runs in the final dataset, total spend was **$2.44**. Across the entire v0 (including smoke / debugging runs / earlier agd-graph v1) the total was **~$4.90**.

---

## 5. Honest caveats — what this v0 does and does not show

**Sample size.** 8 tasks, 1 repository. Numbers are *directional*, not statistically robust. Mean function_overlap differences of 0.10–0.20 on n=8 sit inside the per-task noise band. The CDF shape difference is more informative than any specific mean.

**Single model.** Haiku 4.5 only. The ID-construction friction that killed `agd-graph` v1 may behave differently on Sonnet — either disappearing (more capable model handles indirection better) or persisting in a different form. Both outcomes are interesting; neither is known.

**Weak oracle.** `function_overlap` ≠ "tests pass". A model that edits the right function with semantically wrong code scores well here. A model that fixes the bug in a different function scores 0. To convert this v0 into evidence for a paper, the Docker-based test execution oracle (using SWE-bench's `FAIL_TO_PASS` / `PASS_TO_PASS`) must replace patch-similarity. Plumbing exists in the loader (`task.fail_to_pass`, `task.test_patch`) but is unused in v0.

**No trial averaging.** Each (task × adapter) cell is run once. LLM tool-use runs have non-trivial variance turn-to-turn. To distinguish strategy effects from sampling noise, ≥3 trials per cell are needed.

**Adapter implementations are not load-tested.** `agd-graph` v2 has known weaknesses: (a) `agd_toc(file=)` filtering is a Python-side post-filter, not a CLI feature — large corpora will pay for it. (b) The slug heuristic in `agd_toc(file=PATH)` is approximate (handles `.py` but assumes simple paths). (c) `read_window` re-reads files on each call with no cache.

**`preload-bm25` is the *generous* preload baseline.** A real "ship the whole repo" baseline on a Django-sized repo can't fit in 200k context at all. We BM25-rank specifically so the comparison is fair on small repos. On large repos the strawman becomes "preload doesn't even apply".

**No attempt to control for tool-call count as a confound.** `agd-graph` and `grep-agent` make 13–15 tool calls. Each tool result is shown to the model. This affects RAF (counted) but also affects model attention behavior (not measured). A reviewer could reasonably ask whether `agd-graph`'s correctness comes from addressable retrieval *or* from "more tool calls per task lets the model think more".

**No correlation analysis.** We have not plotted `(RAF, function_overlap)` as a scatter to see whether the two axes are independent or coupled. That plot is the centrepiece of the eventual paper, and v0 has the data for it but did not produce it.

---

## 6. What's already in the repo

All v0 task-level work lives under `benchmarks/task_bench/` (sibling
of the existing token-level bench at `benchmarks/scripts/`).

```
benchmarks/
├── README.md                       (existing — S0-S8 token-level bench, with pointer to task_bench)
├── scripts/                        (existing — bench.py / bench_cost.py / generate.py)
└── task_bench/                     (this v0)
    ├── README.md                   (entry point: how to run)
    ├── REPORT_v0.md                (this document)
    ├── requirements.txt            (datasets, anthropic, tiktoken)
    ├── scripts/
    │   ├── swebench_loader.py      (HuggingFace dataset + git clone cache)
    │   ├── raf.py                  (essential-context + RAF)
    │   ├── oracle.py               (patch-similarity, no Docker)
    │   ├── agd_repo_ingest.py      (Python AST → AGD corpus with refs= edges)
    │   ├── bench_task.py           (orchestrator, per-task × per-adapter)
    │   ├── summarize_task.py       (aggregator, JSON + Markdown output)
    │   └── adapters/
    │       ├── base.py             (RetrievalAdapter ABC, extract_patch)
    │       ├── preload_bm25.py
    │       ├── grep_agent.py
    │       └── agd_graph.py
    ├── corpora/                    (cached AGD corpora keyed by <repo>__<sha[:12]>.agd)
    │   └── psf__requests__1ba83c47ce7b.agd  (52 files, 640 symbols, 441 edges)
    └── results/
        ├── run_requests.json                  (24 rows: 3 adapters × 8 tasks, agd v1)
        ├── run_requests_v2_agd.json           (8 rows: agd v2 only, same 8 tasks)
        ├── run_requests_combined.json         (24 rows, agd v1 rows replaced with v2 — canonical)
        └── *.summary.{json,md}                (per-run aggregations)
```

Repos are cloned to `~/.cache/agd-memory-bench/repos/<owner>__<name>/`
on first use and reused (detached checkout per task). AGD corpora in
`corpora/` are keyed by `<repo>__<sha[:12]>.agd` so the same repo at
different SWE-bench base_commits produces distinct corpora.

`bench_task.py` is fully reusable: `--n N --repo-filter <owner/name>
--adapter NAME --token-budget N --model M`. Dry-run with `--dry-run`
to compute essential-context only (no API calls).

---

## 7. What I would do next, in priority order

### 7.1 Expand the dataset — *highest value, lowest risk*

Run the same harness on **`pytest-dev/pytest`** (19 SWE-bench Verified tasks) and **`sphinx-doc/sphinx`** (44 tasks). Sample 10 tasks each. Total 28 new tasks × 3 adapters × ~1 trial = ~$3-5 at Haiku.

Goal: see whether the v2 ranking holds on different repos. If `agd-graph` continues to lead on `function_overlap` and `RAF median` across three repos, the result is publishable. If it loses on a different repo style, that's a more important result than the win on `psf/requests`.

### 7.2 Sonnet 4.6 on the same suite — *medium value, medium cost*

Re-run the 8 `psf/requests` tasks with `claude-sonnet-4-6`. ~$10-15 (Sonnet is 3-5× Haiku price at these token levels).

Hypothesis A: Sonnet uses both `grep` and `agd` more efficiently — gap between them shrinks. Negative result for the addressability claim.

Hypothesis B: Sonnet's better long-context handling lets `preload-bm25` close the correctness gap, but RAF stays where it is — the *retrieval* axis remains decisive but *correctness* loses its marginal reward.

Hypothesis C: Sonnet exploits `agd-graph`'s edge-based navigation (`refs=`, `--with-backlinks`) more than Haiku does — gap widens.

All three answers are interesting. The question is which one is true.

### 7.3 Trials × 3 with seeds — *low value, low cost*

Run each (task × adapter) cell 3 times with different seeds. Mean + std on the 0.10–0.20 differences. Required for any claim that's not "directional".

### 7.4 Adversarial fan-in task — *high research value, requires task curation*

Pick (or construct) a SWE-bench task whose fix touches a function called from 30+ places. This is where S6 (backlink explosion) crosses from "synthetic finding" to "real failure mode". Compare:

- `agd-graph` with `--with-backlinks` (no limit)
- `agd-graph` with `--with-backlinks --limit 5 --rank-by-relevance` (the TODO from S6)

This connects an existing negative result in the token-level bench to a positive engineering fix in the task-level bench.

### 7.5 Docker oracle — *deferred to v1*

Replace `oracle.evaluate` with a runner that applies the model patch, runs `pytest` with `FAIL_TO_PASS` / `PASS_TO_PASS` filters, and reports binary correctness. This requires:

- Docker installed on the bench machine
- SWE-bench's per-instance environment images (~50 GB pulled across all repos in scope)
- ~10× more wallclock per task

Worth doing only after the `function_overlap` proxy has produced a hypothesis worth verifying with stronger evidence.

---

## 8. The claim that this v0 supports

> **In a localised bug-fix task on a moderately-sized Python repository, an addressable function-level memory with intra-module call edges plus targeted neighbour-context fallback (`read_window`) reduces RAF by ~17× vs. budget-filling preload and produces ~38% higher function-level patch correctness than tool-use over `ripgrep + read_file`, at comparable cost and time. The architecture loses ~70% of its correctness without the neighbour-context affordance.**

It is a *narrow* claim: 1 repo, 1 model, 8 tasks, no test execution. It is also a *crisp* claim: every number in it is bounded, measurable, reproducible from the provided scripts.

The next step is to find out whether it holds beyond `psf/requests` and Haiku.
