# task-bench v0 — claude-haiku-4-5

- instances: **8**, rows: 24, errors: 0

## per adapter

| adapter | n | RAF median | RAF p95 | function_overlap | file_overlap | line_jaccard | off_target | tool_calls | $ total | s/run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `preload-bm25` | 8 | 127.65 | 764.59 | 0.44 | 0.75 | 0.01 | 0.25 | 1 | $0.833 | 9.13 |
| `grep-agent` | 8 | 8.57 | 46.25 | 0.50 | 0.62 | 0.34 | 0.12 | 15.1 | $0.793 | 26.05 |
| `agd-graph` | 8 | 7.86 | 67.79 | 0.00 | 0.25 | 0.00 | 0.38 | 22 | $1.293 | 35.47 |

## RAF CDF (fraction of runs with RAF ≤ threshold)

| RAF ≤ | `preload-bm25` | `grep-agent` | `agd-graph` |
|---:|---:|---:|---:|
| 2 | 0.00 | 0.00 | 0.00 |
| 5 | 0.00 | 0.38 | 0.12 |
| 10 | 0.00 | 0.50 | 0.62 |
| 50 | 0.00 | 1.00 | 0.88 |
| 200 | 0.62 | 1.00 | 1.00 |

## per-task winners (lowest RAF among adapters with function_overlap > 0)

- `psf__requests-1921` → **grep-agent** (RAF=24.92, fn=1.00)
- `psf__requests-2317` → **grep-agent** (RAF=2.76, fn=0.50)
- `psf__requests-5414` → **preload-bm25** (RAF=114.92, fn=1.00)
- `psf__requests-6028` → no adapter solved it
- `psf__requests-1766` → **grep-agent** (RAF=4.39, fn=1.00)
- `psf__requests-2931` → **grep-agent** (RAF=5.2, fn=0.50)
- `psf__requests-1142` → **grep-agent** (RAF=48.26, fn=1.00)
- `psf__requests-1724` → no adapter solved it

