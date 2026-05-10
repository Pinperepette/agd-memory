#!/usr/bin/env bash
# Self-bootstrapping launcher for the agd-memory MCP server.
#
# First run: locate a python3.10+ interpreter, create a private venv,
# install the requirements, then exec the server.
# Subsequent runs: skip straight to exec.
#
# stdout MUST stay clean — it carries JSON-RPC for the MCP client.
# All log output is sent to stderr.

set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/agd-memory/venv"
PY_BIN="$VENV_DIR/bin/python"
SENTINEL="$VENV_DIR/.ready"

if [ -f "$SENTINEL" ] && [ -x "$PY_BIN" ]; then
    exec "$PY_BIN" "$HERE/server.py"
fi

find_python() {
    local cand
    for cand in \
        python3.12 python3.13 python3.11 python3.10 python3 \
        /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 \
        /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
        /usr/local/bin/python3.12 /usr/local/bin/python3.13 \
        /usr/local/bin/python3.11 /usr/local/bin/python3 \
        "$HOME/.pyenv/shims/python3.12" "$HOME/.pyenv/shims/python3" \
        /usr/bin/python3
    do
        local resolved=""
        if [ -x "$cand" ]; then
            resolved="$cand"
        elif command -v "$cand" >/dev/null 2>&1; then
            resolved="$(command -v "$cand")"
        else
            continue
        fi
        if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

echo "agd-memory: first-run bootstrap (creating venv, installing mcp)..." >&2

PY="$(find_python)" || {
    echo "agd-memory: no python >= 3.10 found. Install python3.10+ and restart." >&2
    exit 1
}

mkdir -p "$(dirname "$VENV_DIR")"
"$PY" -m venv "$VENV_DIR" 1>&2 || {
    echo "agd-memory: failed to create venv at $VENV_DIR" >&2
    exit 1
}

REQS="$REPO_ROOT/requirements.txt"
if [ -f "$REQS" ]; then
    "$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check -r "$REQS" 1>&2 || {
        echo "agd-memory: pip install failed (see stderr)" >&2
        exit 1
    }
else
    "$VENV_DIR/bin/pip" install --quiet --disable-pip-version-check 'mcp>=0.9.0' 1>&2 || {
        echo "agd-memory: pip install failed (see stderr)" >&2
        exit 1
    }
fi

touch "$SENTINEL"
echo "agd-memory: bootstrap complete, starting server" >&2

exec "$PY_BIN" "$HERE/server.py"
