#!/usr/bin/env bash
# UserPromptSubmit hook: invokes `agd-memory-recall.py` to inject the
# memory blocks most relevant to the user's prompt.
#
# Mirrors the python-discovery logic of mcp_servers/run.sh — prefers the
# plugin's private venv (already populated for the MCP server) and falls
# back to system python3.10+.
#
# stdin from the Claude Code hook runtime is piped through unchanged.
# Errors are swallowed: this hook MUST NEVER break the user's prompt,
# so we always exit 0.

set +e

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SCRIPT="$HERE/agd-memory-recall.py"

if [ ! -f "$SCRIPT" ]; then
    exit 0
fi

VENV_DIR="${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/agd-memory}/venv"
VENV_PY="$VENV_DIR/bin/python"
SENTINEL="$VENV_DIR/.ready"

if [ -f "$SENTINEL" ] && [ -x "$VENV_PY" ]; then
    "$VENV_PY" "$SCRIPT"
    exit 0
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

PY="$(find_python)" || exit 0
"$PY" "$SCRIPT"
exit 0
