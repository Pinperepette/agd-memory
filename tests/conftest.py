"""Shared fixtures for agd-memory tests.

Two fixtures cover both `tests/test_server.py` and `tests/test_recall.py`:

  * `tmp_memory` — writes a minimal `memory.agd` to `tmp_path` and points
    `AGD_MEMORY_FILE` at it (honoured by `server.py:71-77` and the recall
    module).
  * `fake_agd` — installs the test double from `tests/fixtures/fake_agd.py`
    as `AGD_BIN`. Exposes `write_fixture(data)` to set the dataset and
    `read_log()` to inspect each invocation's argv (used to assert on the
    `--op` JSON passed to `agd edit`).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture
def recall(tmp_path, monkeypatch):
    """Load `hooks/agd-memory-recall.py` as an importable module.

    The dashed filename can't be imported directly. We load it via
    `importlib.util.spec_from_file_location`, register it in
    `sys.modules` before `exec_module` so `@dataclass` can resolve
    `cls.__module__` during class body evaluation.

    Also isolates the persistent config dir to `tmp_path` so tests
    don't pollute (or read from) the user's real `recall.json`.
    """
    import sys

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin_data"))
    monkeypatch.delenv("AGD_RECALL_LANGS", raising=False)
    monkeypatch.delenv("AGD_RECALL_STOPWORDS_FILE", raising=False)

    name = "agd_memory_recall"
    path = _HOOKS / "agd-memory-recall.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(name, None)


@pytest.fixture
def fake_agd(tmp_path, monkeypatch):
    fake_bin = _FIXTURES / "fake_agd.py"
    if not fake_bin.is_file():
        raise RuntimeError(f"missing fake binary: {fake_bin}")
    fixture_path = tmp_path / "agd_fixture.json"
    log_path = tmp_path / "agd_log.jsonl"
    fixture_path.write_text(json.dumps({"blocks": []}))
    log_path.write_text("")

    monkeypatch.setenv("AGD_BIN", str(fake_bin))
    monkeypatch.setenv("FAKE_AGD_FIXTURE", str(fixture_path))
    monkeypatch.setenv("FAKE_AGD_LOG", str(log_path))

    def write_fixture(data: dict) -> None:
        fixture_path.write_text(json.dumps(data))

    def read_log() -> list[list[str]]:
        if not log_path.is_file():
            return []
        out = []
        for line in log_path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def calls(subcommand: str) -> list[list[str]]:
        return [argv for argv in read_log() if argv and argv[0] == subcommand]

    return SimpleNamespace(
        bin=fake_bin,
        fixture_path=fixture_path,
        log_path=log_path,
        write_fixture=write_fixture,
        read_log=read_log,
        calls=calls,
    )


@pytest.fixture
def tmp_memory(tmp_path, monkeypatch):
    mem = tmp_path / "memory.agd"
    mem.write_text("@meta format=agd version=1 [#meta]\n")
    monkeypatch.setenv("AGD_MEMORY_FILE", str(mem))
    return mem
