#!/usr/bin/env bash
# SessionStart hook: if the current project has an AGD memory file,
# inject its Table of Contents into the session context.
#
# Path convention:
#   ~/.claude/projects/<sanitized-cwd>/memory/memory.agd
# where sanitized-cwd is the working directory with `/` replaced by `-`.
#
# Output: a single block of text on stdout. Claude Code's SessionStart
# hook merges hook stdout into the session system context.
# Empty output = no memory for this project = silent no-op.

set -e

AGD_BIN="${AGD_BIN:-$HOME/.cargo/bin/agd}"
[ -x "$AGD_BIN" ] || exit 0  # agd not installed → silent no-op

# Sanitize cwd → memory dir
CWD="$(pwd)"
SANITIZED="${CWD//\//-}"
MEM_DIR="$HOME/.claude/projects/${SANITIZED}/memory"
MEM_FILE="$MEM_DIR/memory.agd"

[ -f "$MEM_FILE" ] || exit 0  # no memory for this project → silent no-op

# Validate quickly; if invalid, abort silently.
"$AGD_BIN" validate "$MEM_FILE" >/dev/null 2>&1 || exit 0

TOC="$("$AGD_BIN" ids "$MEM_FILE" --json 2>/dev/null)"

if [ -n "$TOC" ] && [ "$TOC" != "[]" ]; then
    DISPLAY="${MEM_FILE/#$HOME/~}"
    cat <<EOF
[agd-memory] Project memory available at \`${DISPLAY}\`.
Use the agd-memory skill or call MCP tools to retrieve specific blocks.
Don't load the whole document — pull only what's needed.

TOC (machine-readable):
\`\`\`json
${TOC}
\`\`\`

CLI fallback:
\`\`\`sh
agd ids "${DISPLAY}"
agd ids "${DISPLAY}" --kind x-feedback
agd get "${DISPLAY}" '#some-id'
\`\`\`
EOF
fi

exit 0
