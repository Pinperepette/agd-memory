"""The SessionEnd distiller hook.

Exercised through the real shell script with a stubbed `claude` on PATH,
because the behaviour worth pinning lives in the shell: the gate that
decides whether an LLM call is worth spending, the recursion guard, the
retry, and the lock that keeps two distillers from racing.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / "hooks" / "agd-memory-distill.sh"


def _lock_key(cwd: Path) -> str:
    """Mirror the hook's lock key: sha1 of the *resolved memory file*.

    Resolved through the same helper the hook and every writer use, so
    the test cannot drift from the implementation by guessing the path.
    """
    sys.path.insert(0, str(_ROOT / "lib"))
    from agd_memory_paths import project_memory_file
    env = dict(os.environ, AGD_MEMORY_PROJECT_CWD=str(cwd))
    env.pop("AGD_MEMORY_FILE", None)
    mem = project_memory_file(env)
    return hashlib.sha1(str(mem).encode()).hexdigest()[:12]


def _write_stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "claude"
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(0o755)


def _transcript(path: Path, *, tool_uses: int = 0, edits: int = 0) -> Path:
    rows = []
    for _ in range(edits):
        rows.append({"message": {"content": [{"type": "tool_use", "name": "Edit"}]}})
    for _ in range(tool_uses):
        rows.append({"message": {"content": [{"type": "tool_use", "name": "Read"}]}})
    if not rows:
        rows = [{"message": {"content": [{"type": "text", "text": "ciao"}]}}]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


@pytest.fixture
def hook(tmp_path):
    """Returns fire(session_id, transcript, **env) -> CompletedProcess."""
    state = tmp_path / "state"
    state.mkdir()
    bin_dir = tmp_path / "bin"
    _write_stub(bin_dir, 'echo "STUB ran $$"\n')

    def fire(
        session: str, transcript: Path, cwd: Path | None = None, **extra_env
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["CLAUDE_PLUGIN_DATA"] = str(state)
        env.update(extra_env)
        payload = json.dumps({
            "transcript_path": str(transcript),
            "cwd": str(cwd or tmp_path),
            "session_id": session,
        })
        return subprocess.run(
            ["bash", str(_HOOK)], input=payload, text=True,
            capture_output=True, env=env, timeout=60,
        )

    def log() -> str:
        p = state / "distill.log"
        return p.read_text() if p.is_file() else ""

    fire.log = log
    fire.state = state
    fire.bin_dir = bin_dir
    return fire


def _wait_for(predicate, timeout=20.0):
    """The hook detaches its work, so results arrive after it returns."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_hook_always_exits_zero_and_never_blocks_teardown(hook, tmp_path):
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    assert hook("s1", t).returncode == 0


def test_gate_skips_a_trivial_session(hook, tmp_path):
    """No edits and too few tool uses: not worth an LLM call."""
    t = _transcript(tmp_path / "t.jsonl")
    hook("trivial", t)
    time.sleep(1.0)
    assert "STUB ran" not in hook.log()


def test_gate_runs_when_the_session_edited_a_file(hook, tmp_path):
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("rich", t)
    assert _wait_for(lambda: "STUB ran" in hook.log())


def test_recursion_guard_blocks_the_headless_child(hook, tmp_path):
    """The distiller's own `claude -p` run fires SessionEnd again."""
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("child", t, AGD_DISTILL_GUARD="1")
    time.sleep(1.0)
    assert "STUB ran" not in hook.log()


def test_disabled_via_env(hook, tmp_path):
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("off", t, AGD_DISTILL_DISABLED="1")
    time.sleep(1.0)
    assert "STUB ran" not in hook.log()


def test_only_one_distiller_runs_at_a_time(hook, tmp_path):
    """Two SessionEnd hooks on one session — a plugin install plus a
    leftover hand-installed one — must not both spend a call and both
    write the same memory file."""
    _write_stub(hook.bin_dir, 'echo "STUB ran $$"\nsleep 3\n')
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("A", t)
    hook("B", t)
    assert _wait_for(lambda: "holds the lock" in hook.log())
    time.sleep(4)
    assert hook.log().count("STUB ran") == 1


def test_two_different_projects_distil_concurrently(hook, tmp_path):
    """The lock protects one memory file, not the whole machine. An
    unrelated repo ending its session must not cancel this one's
    distillation — that would be the silent loss the retry exists to
    prevent, caused by a project you weren't even working on."""
    _write_stub(hook.bin_dir, 'echo "STUB ran $$"\nsleep 3\n')
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    other = tmp_path / "other-project"
    other.mkdir()
    hook("proj-a", t)
    hook("proj-b", t, cwd=other)
    assert _wait_for(lambda: hook.log().count("STUB ran") == 2)
    assert "holds the lock" not in hook.log()


def test_two_checkouts_of_one_repo_share_the_lock(hook, tmp_path, monkeypatch):
    """The reason the key is the resolved memory file and not the cwd.

    Two checkouts of one repository sit at different paths but
    `project_memory_file()` maps them onto the same memory.agd through
    the git-remote index. A cwd-keyed lock let both distil it, racing
    the dedup pass and producing duplicate blocks.
    """
    home = tmp_path / "home"
    checkout_a = tmp_path / "a"
    checkout_b = tmp_path / "b"
    remote = "https://github.com/example/shared.git"
    for co in (checkout_a, checkout_b):
        co.mkdir()
        subprocess.run(["git", "init", "-q", str(co)], check=True)
        subprocess.run(
            ["git", "-C", str(co), "remote", "add", "origin", remote], check=True
        )
    # Give checkout A a memory file, so B resolves to it via the index.
    mem_a = (home / ".claude" / "projects"
             / str(checkout_a.resolve()).replace("/", "-") / "memory")
    mem_a.mkdir(parents=True)
    (mem_a / "memory.agd").write_text("@meta format=agd version=1 [#meta]\n")

    env = {"AGD_MEMORY_HOME": str(home)}
    sys.path.insert(0, str(_ROOT / "lib"))
    from agd_memory_paths import project_memory_file
    resolved_a = project_memory_file(dict(env, AGD_MEMORY_PROJECT_CWD=str(checkout_a)))
    resolved_b = project_memory_file(dict(env, AGD_MEMORY_PROJECT_CWD=str(checkout_b)))
    assert resolved_a == resolved_b, "precondition: both checkouts share one memory"

    _write_stub(hook.bin_dir, 'echo "STUB ran $$"\nsleep 3\n')
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("checkout-a", t, cwd=checkout_a, **env)
    hook("checkout-b", t, cwd=checkout_b, **env)
    assert _wait_for(lambda: "holds the lock" in hook.log())
    time.sleep(4)
    assert hook.log().count("STUB ran") == 1


def test_lock_is_released_so_the_next_session_can_distil(hook, tmp_path):
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("first", t)
    assert _wait_for(lambda: "STUB ran" in hook.log())
    lock = hook.state / f"distill-{_lock_key(tmp_path)}.lock"
    assert _wait_for(lambda: not lock.exists())
    hook("second", t)
    assert _wait_for(lambda: hook.log().count("STUB ran") == 2)


def test_stale_lock_is_broken_rather_than_wedging_forever(hook, tmp_path):
    """A run killed before its trap fired must not disable distillation."""
    lock = hook.state / f"distill-{_lock_key(tmp_path)}.lock"
    lock.mkdir(parents=True)
    os.utime(lock, (0, 0))
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("after-crash", t)
    assert _wait_for(lambda: "breaking stale lock" in hook.log())
    assert _wait_for(lambda: "STUB ran" in hook.log())


def test_transient_failure_is_retried(hook, tmp_path):
    """A 529 used to drop the session's knowledge silently."""
    _write_stub(hook.bin_dir, 'echo "STUB ran $$"\nexit 1\n')
    t = _transcript(tmp_path / "t.jsonl", edits=1)
    hook("flaky", t, AGD_DISTILL_RETRIES="2", AGD_DISTILL_BACKOFF_S="1")
    assert _wait_for(lambda: "giving up" in hook.log())
    assert hook.log().count("STUB ran") == 2
