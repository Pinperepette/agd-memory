# task-bench v0 — claude-haiku-4-5

- instances: **10**, rows: 10, errors: 0

## per adapter

| adapter | n | RAF median | RAF p95 | function_overlap | file_overlap | line_jaccard | off_target | tool_calls | $ total | s/run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `agd-graph` | 10 | 26.09 | 166.31 | 0.76 | 0.90 | 0.49 | 0 | 14.5 | $1.707 | 30.61 |

## RAF CDF (fraction of runs with RAF ≤ threshold)

| RAF ≤ | `agd-graph` |
|---:|---:|
| 2 | 0.00 |
| 5 | 0.00 |
| 10 | 0.00 |
| 50 | 0.80 |
| 200 | 0.90 |

## per-task winners (lowest RAF among adapters with function_overlap > 0)

- `pytest-dev__pytest-7490` → no adapter solved it
- `pytest-dev__pytest-7432` → **agd-graph** (RAF=10.47, fn=1.00)
- `pytest-dev__pytest-5631` → **agd-graph** (RAF=44.43, fn=1.00)
- `pytest-dev__pytest-6202` → **agd-graph** (RAF=22.34, fn=1.00)
- `pytest-dev__pytest-5787` → **agd-graph** (RAF=16.62, fn=0.25)
- `pytest-dev__pytest-7521` → **agd-graph** (RAF=26.2, fn=1.00)
- `pytest-dev__pytest-7982` → **agd-graph** (RAF=224.34, fn=1.00)
- `pytest-dev__pytest-5809` → **agd-graph** (RAF=12.83, fn=1.00)
- `pytest-dev__pytest-7324` → **agd-graph** (RAF=25.99, fn=0.33)
- `pytest-dev__pytest-7571` → **agd-graph** (RAF=35.94, fn=1.00)

