# task-bench v0 — claude-haiku-4-5

- instances: **8**, rows: 8, errors: 0

## per adapter

| adapter | n | RAF median | RAF p95 | function_overlap | file_overlap | line_jaccard | off_target | tool_calls | $ total | s/run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `agd-graph` | 8 | 7.43 | 92.09 | 0.69 | 0.88 | 0.43 | 0.25 | 13.1 | $0.814 | 32.61 |

## RAF CDF (fraction of runs with RAF ≤ threshold)

| RAF ≤ | `agd-graph` |
|---:|---:|
| 2 | 0.00 |
| 5 | 0.38 |
| 10 | 0.62 |
| 50 | 0.88 |
| 200 | 1.00 |

## per-task winners (lowest RAF among adapters with function_overlap > 0)

- `psf__requests-1921` → **agd-graph** (RAF=12.93, fn=1.00)
- `psf__requests-2317` → **agd-graph** (RAF=4.71, fn=0.50)
- `psf__requests-5414` → **agd-graph** (RAF=7.0, fn=1.00)
- `psf__requests-6028` → no adapter solved it
- `psf__requests-1766` → **agd-graph** (RAF=4.97, fn=1.00)
- `psf__requests-2931` → **agd-graph** (RAF=3.47, fn=0.50)
- `psf__requests-1142` → **agd-graph** (RAF=120.05, fn=1.00)
- `psf__requests-1724` → **agd-graph** (RAF=7.86, fn=0.50)

