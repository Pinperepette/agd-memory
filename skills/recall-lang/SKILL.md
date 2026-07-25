---
name: recall-lang
description: "Configure the language(s) used by the agd-memory auto-recall hook for stopword filtering. Use when the user says \"enable italian recall\", \"abilita italiano per il recall\", \"set recall language\", \"which languages for recall\", \"disabilita italiano recall\", \"reset recall languages\", or asks to add a custom stopwords file. Default after install is English only; other languages are opt-in."
license: MIT
metadata:
  author: pinperepette
  version: "0.1.0"
  domain: tooling
  triggers: enable italian recall, abilita italiano recall, disable italian recall, disabilita italiano recall, set recall language, lingua recall, which languages for recall, che lingue ha recall, reset recall, recall stopwords file, recall config, custom stopwords
  role: utility
  scope: configuration
---

# AGD Memory recall — language configuration

The auto-recall hook (UserPromptSubmit) scores memory blocks against the
user's prompt and injects the top few. To avoid false matches on
function words ("the", "and", "che", "del"), each prompt is filtered
against a stopwords list.

Default after install: **English only**. Other languages are opt-in via
this skill (or directly by env var / CLI). Built-in lists: `en`, `it`.

A persistent config file lives at:

```
${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/agd-memory}/recall.json
```

The hook reads it at every prompt. The env var `AGD_RECALL_LANGS` always
wins over the config file when set.

## CLI

All commands run through the recall script's `config` subcommand. Invoke
it via Bash (the script auto-resolves Python via the plugin's venv or
system python3.10+):

```sh
bash "$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.sh" <impossible>  # NOT this
# Instead, invoke the python module directly:
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config show
```

If `$CLAUDE_PLUGIN_ROOT` is not set, look for the plugin under
`~/.claude/plugins/marketplaces/agd-memory/` (default marketplace
install location).

Subcommands:

| Command | Effect |
| --- | --- |
| `config show` | print the current langs, source (env/file/default), and the path of the config file |
| `config enable <lang>` | add a built-in language (`en`, `it`) |
| `config disable <lang>` | remove a language from the list |
| `config set <l1,l2,...>` | replace the list entirely |
| `config reset` | delete the config file, fall back to default (`en`) |
| `config stopwords-file <path>` | also load custom stopwords from a file (one word per line, `#` comments allowed) |
| `config stopwords-file` (no arg) | clear the custom file |

## How to handle user requests

**"abilita italiano per il recall" / "enable italian recall"**

```sh
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config enable it
```

Tell the user: from the next prompt, Italian stopwords ("che", "del",
"sono", etc.) are filtered before scoring.

**"disabilita italiano" / "disable italian recall"**

```sh
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config disable it
```

**"che lingue ho per recall?" / "show recall config"**

```sh
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config show
```

Read the output back to the user verbatim.

**"reset recall" / "torna al default"**

```sh
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config reset
```

**"aggiungi parole personali al recall" / "use my own stopwords file"**

Ask the user for the path. Verify the file exists, then:

```sh
"$CLAUDE_PLUGIN_ROOT/hooks/agd-memory-recall.py" config stopwords-file /path/to/words.txt
```

Each non-empty, non-comment line is a stopword. The file is read every
time the hook fires (no daemon, no caching).

## Notes

- The hook tokenizer is Unicode-aware: scripts other than Latin
  (Cyrillic, Greek, CJK, etc.) tokenize correctly out of the box —
  only the stopword list determines what gets filtered. Add stopwords
  via `config stopwords-file` for any language not in the built-in set.
- Changes take effect **on the next prompt**, no restart needed.
- For a one-off override without persisting, set `AGD_RECALL_LANGS=it,en`
  in the shell before launching `claude`. The env var beats the file.
