# task-bench v0 — claude-haiku-4-5

- instances: **10**, rows: 30, errors: 0

## per adapter

| adapter | n | RAF median | RAF p95 | function_overlap | file_overlap | line_jaccard | off_target | tool_calls | $ total | s/run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `preload-bm25` | 10 | 367.95 | 702.78 | 0.30 | 0.80 | 0.06 | 0.3 | 1 | $1.093 | 13.6 |
| `grep-agent` | 10 | 14.34 | 45.13 | 0.72 | 1.00 | 0.55 | 0.1 | 11.2 | $0.904 | 23.12 |
| `agd-graph` | 10 | 28.62 | 85.04 | 0.70 | 0.70 | 0.48 | 0 | 16.2 | $1.328 | 28.33 |

## RAF CDF (fraction of runs with RAF ≤ threshold)

| RAF ≤ | `preload-bm25` | `grep-agent` | `agd-graph` |
|---:|---:|---:|---:|
| 2 | 0.00 | 0.00 | 0.00 |
| 5 | 0.00 | 0.00 | 0.10 |
| 10 | 0.00 | 0.20 | 0.20 |
| 50 | 0.00 | 0.90 | 0.60 |
| 200 | 0.20 | 1.00 | 1.00 |

## per-task winners (lowest RAF among adapters with function_overlap > 0)

- `pytest-dev__pytest-7490` → no adapter solved it
- `pytest-dev__pytest-7432` → **grep-agent** (RAF=7.35, fn=1.00)
- `pytest-dev__pytest-5631` → **grep-agent** (RAF=13.8, fn=1.00)
- `pytest-dev__pytest-6202` → **grep-agent** (RAF=14.88, fn=1.00)
- `pytest-dev__pytest-5787` → **grep-agent** (RAF=8.84, fn=0.25)
- `pytest-dev__pytest-7521` → **grep-agent** (RAF=22.93, fn=1.00)
- `pytest-dev__pytest-7982` → **grep-agent** (RAF=11.37, fn=1.00)
- `pytest-dev__pytest-5809` → **agd-graph** (RAF=10.49, fn=1.00)
- `pytest-dev__pytest-7324` → **grep-agent** (RAF=20.85, fn=0.33)
- `pytest-dev__pytest-7571` → **grep-agent** (RAF=29.38, fn=0.67)

