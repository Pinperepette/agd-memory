"""Where memory lives — shared by the MCP server and the recall hook.

Both entry points used to resolve paths themselves, with the same rule
copied twice. Two layers exist:

  * **project** — facts about one codebase. Keyed by the working
    directory, as before, but a git remote (when there is one) is
    recorded in an index so the *same repository* opened from a
    different checkout path resolves to the memory it already has.
    Measured motivation: this very repo had accumulated two disjoint
    8-block memories under two different paths.

  * **global** — durable facts about the user that hold everywhere:
    preferences, prose style, standing rules. Previously these had to be
    re-learned per project, or lived in whichever project happened to be
    open at `$HOME`.

Nothing moves on disk. The index only *points at* the file a project
already uses, so an installation that predates this module keeps working
and simply stops forking a new silo per path.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Mapping

__all__ = [
    "project_memory_file",
    "global_memory_file",
    "git_remote",
    "index_path",
]

_GIT_TIMEOUT_S = 2


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("AGD_MEMORY_HOME") or Path.home())


def index_path(env: Mapping[str, str]) -> Path:
    """Maps `git remote url -> memory file` for already-seen repositories."""
    return _home(env) / ".claude" / "projects" / ".agd-memory-index.json"


def global_memory_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    override = env.get("AGD_MEMORY_GLOBAL_FILE")
    if override:
        return Path(override).expanduser()
    return _home(env) / ".claude" / "memory" / "global.agd"


def _path_keyed(cwd: Path, env: Mapping[str, str]) -> Path:
    sanitized = str(cwd).replace("/", "-")
    return _home(env) / ".claude" / "projects" / sanitized / "memory" / "memory.agd"


def git_remote(cwd: Path) -> str | None:
    """The repository's canonical remote, or None outside a repo.

    Failures are swallowed: no git, no remote, a detached worktree or a
    slow filesystem must all degrade to path-keyed resolution rather
    than break memory lookup.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    url = out.stdout.strip()
    if not url:
        return None
    # Normalise the two spellings of the same GitHub repo so an ssh
    # checkout and an https one share a memory.
    for prefix in ("git@github.com:", "https://github.com/", "ssh://git@github.com/"):
        if url.startswith(prefix):
            url = "github.com/" + url[len(prefix):]
            break
    return url.removesuffix(".git").rstrip("/")


def _read_index(env: Mapping[str, str]) -> dict:
    try:
        with index_path(env).open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _record(env: Mapping[str, str], remote: str, mem: Path) -> None:
    """Best-effort: a memory lookup must never fail because of the index."""
    try:
        path = index_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read_index(env)
        data[remote] = str(mem)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        tmp.replace(path)
    except OSError:
        pass


def project_memory_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve this project's memory file. Does not create anything.

    Order: explicit override, then the path-keyed file if it already
    exists, then whatever this repository is known to use, then the
    path-keyed location to be created.

    An existing path-keyed file always wins. That ordering matters: the
    index must only ever *add* reach, never take it away. If a repo has
    two path-keyed silos from two checkouts, both keep resolving to
    their own content — consolidating them is an explicit choice, made
    with `scripts/merge_memory.py`, not a side effect of a lookup. What
    the index does buy is a fresh checkout of a known repo inheriting
    the memory it already has instead of starting empty.
    """
    env = os.environ if env is None else env
    override = env.get("AGD_MEMORY_FILE")
    if override:
        return Path(override).expanduser()

    cwd = Path(env.get("AGD_MEMORY_PROJECT_CWD") or os.getcwd()).resolve()
    fallback = _path_keyed(cwd, env)
    remote = git_remote(cwd)
    if not remote:
        return fallback

    if fallback.is_file():
        _record(env, remote, fallback)
        return fallback

    known = _read_index(env).get(remote)
    if isinstance(known, str) and known and Path(known).is_file():
        return Path(known)
    return fallback
