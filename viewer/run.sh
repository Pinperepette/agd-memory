#!/usr/bin/env bash
# Launcher for the agd-memory viewer. Self-locates the bundled viewer.py,
# picks an interpreter, opens the browser. Pass an optional path argument
# to view a memory file other than the current project's.

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"

find_python() {
    local cand
    for cand in \
        python3 python3.13 python3.12 python3.11 python3.10 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3
    do
        if command -v "$cand" >/dev/null 2>&1; then
            local resolved
            resolved="$(command -v "$cand")"
            if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
                printf '%s\n' "$resolved"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(find_python)" || {
    echo "agd-memory viewer: no python >= 3.9 found in PATH." >&2
    exit 1
}

exec "$PY" "$HERE/viewer.py" "$@"
