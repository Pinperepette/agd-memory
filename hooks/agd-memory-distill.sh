#!/usr/bin/env bash
# SessionEnd hook: distill durable knowledge from the just-ended session
# into this project's AGD memory, via a headless `claude -p` pass.
#
# This is the write half of the plugin. Auto-recall reads memory on every
# prompt; without this, nothing ever gets written unless the user says
# /remember. A second brain has to consolidate on its own.
#
# Design notes:
#  - RECURSION GUARD: the headless run itself fires SessionEnd. We export
#    AGD_DISTILL_GUARD=1 when launching it and bail if it is already set.
#  - GATE: only spend an LLM call when the session did substantial work.
#  - DETACHED: runs in the background so session teardown never blocks.
#  - RETRY: transient API failures (529 Overloaded) used to drop the
#    session's knowledge silently — measured at 10 of 176 runs on the
#    live install. We now retry with a backoff before giving up.
#  - MUTUAL EXCLUSION: only one distiller may run at a time. Two of them
#    racing is not hypothetical — this plugin registers SessionEnd, and a
#    user who also kept the older hand-installed hook in
#    ~/.claude/settings.json gets both firing on the same session, each
#    spending an LLM call and writing the same memory.agd. The recursion
#    guard does not help there: sibling hooks both see it unset.
set -uo pipefail

[ -n "${AGD_DISTILL_GUARD:-}" ] && exit 0
[ -n "${AGD_DISTILL_DISABLED:-}" ] && exit 0

HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(dirname "$HERE")"
STATE_DIR="${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/agd-memory}"
LOG="$STATE_DIR/distill.log"
AGD_BIN="${AGD_BIN:-$HOME/.cargo/bin/agd}"
MODEL="${AGD_DISTILL_MODEL:-claude-sonnet-4-6}"
PROMPT_FILE="$PLUGIN_ROOT/hooks/agd-distill-prompt.md"
RETRIES="${AGD_DISTILL_RETRIES:-3}"
BACKOFF_S="${AGD_DISTILL_BACKOFF_S:-30}"   # grows linearly per attempt

command -v claude >/dev/null 2>&1 || exit 0
[ -x "$AGD_BIN" ] || exit 0
[ -f "$PROMPT_FILE" ] || exit 0
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0

INPUT="$(cat || true)"
read -r TRANSCRIPT CWD SESSION < <(
  printf '%s' "$INPUT" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get("transcript_path","") or "-",
      d.get("cwd","") or ".",
      d.get("session_id","?") or "?")
' 2>/dev/null || echo "- . ?"
)

[ "$TRANSCRIPT" = "-" ] && exit 0
[ -f "$TRANSCRIPT" ] || exit 0

# Gate: skip trivial sessions. Require a file edit, or several tool uses.
GATE="$(python3 -c '
import json,sys
edits = tooluses = 0
try:
    with open(sys.argv[1]) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            content = m.get("content") or (m.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for p in content:
                if isinstance(p, dict) and p.get("type") == "tool_use":
                    tooluses += 1
                    if p.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        edits += 1
except Exception:
    pass
print("go" if (edits > 0 or tooluses >= 4) else "skip")
' "$TRANSCRIPT" 2>/dev/null || echo skip)"

[ "$GATE" = "go" ] || exit 0

PROMPT="$(cat "$PROMPT_FILE")"
LOCK="$STATE_DIR/distill.lock"
STALE_S="${AGD_DISTILL_LOCK_STALE_S:-1800}"
# A subshell, not a `{ }` group: bash 3.2 (what macOS ships) does not
# run an EXIT trap set inside an asynchronous brace group, so the lock
# would never be released. Verified on 3.2.57.
(
  # `mkdir` is atomic on POSIX; macOS has no flock(1), so this is the
  # portable way to make the lock a real one rather than a check-then-act
  # race. A lock older than STALE_S belonged to a run that was killed
  # before its trap fired — break it rather than wedging distillation
  # forever.
  if ! mkdir "$LOCK" 2>/dev/null; then
    lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$lock_age" -gt "$STALE_S" ]; then
      echo "=== $SESSION: breaking stale lock (${lock_age}s old) ==="
      rm -rf "$LOCK"
      mkdir "$LOCK" 2>/dev/null || exit 0
    else
      echo "=== $SESSION cwd=$CWD: another distiller holds the lock, skipping ==="
      exit 0
    fi
  fi
  trap 'rm -rf "$LOCK"' EXIT
  echo "$$" > "$LOCK/pid" 2>/dev/null || true

  echo "=== $SESSION cwd=$CWD $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  attempt=1
  while [ "$attempt" -le "$RETRIES" ]; do
    if AGD_DISTILL_GUARD=1 \
       AGD_MEMORY_PROJECT_CWD="$CWD" \
       AGD_MEMORY_PLUGIN_ROOT="$PLUGIN_ROOT" \
       TRANSCRIPT="$TRANSCRIPT" \
       claude -p "$PROMPT" --model "$MODEL" --dangerously-skip-permissions 2>&1
    then
      break
    fi
    # A 529/overload leaves the session's knowledge unwritten. Back off
    # and try again rather than losing it silently.
    if [ "$attempt" -lt "$RETRIES" ]; then
      backoff=$((attempt * BACKOFF_S))
      echo "distiller attempt $attempt failed; retrying in ${backoff}s"
      sleep "$backoff"
    else
      echo "distiller exited non-zero after $RETRIES attempt(s); giving up"
    fi
    attempt=$((attempt + 1))
  done
) >>"$LOG" 2>&1 &

disown 2>/dev/null || true
exit 0
