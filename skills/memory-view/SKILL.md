---
name: memory-view
description: "Open the agd-memory project memory in a local browser viewer. Spins up a small http server on localhost and prints the URL. Use when the user says \"show me the memory\", \"view memory\", \"open memory\", \"see the graph\", \"apri la memoria\", \"vedi la memoria\", or asks to inspect/visualize/browse the agd memory file."
license: MIT
metadata:
  author: pinperepette
  version: "0.1.0"
  domain: tooling
  triggers: view memory, open memory, show memory, visualize memory, memory graph, browse memory, apri la memoria, vedi la memoria, mostra la memoria, grafo della memoria
  role: utility
  scope: launcher
---

# AGD Memory Viewer

Open a local web page that shows the project's agd-memory as:

- searchable list of all blocks (filterable by `kind`)
- live force-directed graph (nodes = blocks, edges = `refs`)
- body rendering pane with backlinks and refs navigation

The viewer reads from the same memory file the rest of the plugin uses:

```
~/.claude/projects/<sanitized-cwd>/memory/memory.agd
```

## How to launch

The plugin ships a launcher at `viewer/run.sh` next to the MCP server.
From a session, run it in the background and surface the URL to the user:

```sh
"$CLAUDE_PLUGIN_ROOT/viewer/run.sh" >/tmp/agd-viewer.log 2>&1 &
```

If `$CLAUDE_PLUGIN_ROOT` is not set, look under
`~/.claude/plugins/agd-memory/viewer/run.sh` (Claude Code default install
location). Wait ~1 second, then read the URL from the log:

```sh
grep -oE 'http://127\.0\.0\.1:[0-9]+' /tmp/agd-viewer.log | head -1
```

Print that URL to the user. Tell them Ctrl+C in the terminal stops the
server, or kill the background PID.

## Optional: view a different file

`run.sh path/to/memory.agd` — view any AGD file, not only the current
project's memory. Useful for inspecting backups or other projects'
memories.

## What the page shows

- **Top bar**: file path, block count, edge count, kind count, file size.
- **Left pane**: list of blocks. Search box matches id, desc, body.
  Kind pills toggle filters.
- **Center**: graph. Drag to pan, scroll to zoom, click a node to focus.
  Buttons `fit` and `relayout`.
- **Right pane**: selected block — kind tag, id, desc, attrs, rendered
  body (Markdown), refs, backlinks. Refs and backlinks are clickable
  for graph navigation.

Keyboard: `/` focuses search, `Esc` clears selection.
