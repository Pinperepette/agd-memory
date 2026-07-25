"""The write path used by the SessionEnd distiller.

`lib/agd_memory_write.py` is the single implementation shared by the MCP
server and `scripts/agd_mem_save.py`. The server's behaviour is covered
in `test_server.py`; these tests pin the CLI front-end and the pieces
only it exercises — argument plumbing, bootstrap, exit codes.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def writer():
    sys.path.insert(0, str(_ROOT / "lib"))
    return _load("agd_memory_write", _ROOT / "lib" / "agd_memory_write.py")


@pytest.fixture
def cli(monkeypatch, tmp_path):
    sys.path.insert(0, str(_ROOT / "lib"))
    monkeypatch.setenv("AGD_MEMORY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGD_MEMORY_PROJECT_CWD", str(tmp_path / "proj"))
    monkeypatch.setenv("AGD_MEMORY_TODAY", "2026-07-25")
    monkeypatch.delenv("AGD_MEMORY_FILE", raising=False)
    (tmp_path / "proj").mkdir(parents=True, exist_ok=True)
    return _load("agd_mem_save", _ROOT / "scripts" / "agd_mem_save.py")


def _agd_available() -> bool:
    return (Path.home() / ".cargo/bin/agd").is_file()


requires_agd = pytest.mark.skipif(
    not _agd_available(), reason="agd binary not installed"
)


# -- validation, no binary needed ---------------------------------------------

def test_reject_unfencable_flags_standalone_tilde(writer):
    assert writer.reject_unfencable("a\n~~~\nb") is not None
    assert writer.reject_unfencable("a\n  ~~~\nb") is None


def test_reject_invalid_block_suggests_a_usable_id(writer):
    reason = writer.reject_invalid_block("x-project", "vers.1.9.0")
    assert reason is not None and "vers-1-9-0" in reason


def test_extract_refs_ignores_unresolvable_citations(writer):
    known = {"real-block"}
    got = writer.extract_refs(
        "fixes #1, colour #fff, see #real-block and #ghost", known, "self"
    )
    assert got == ["real-block"]


def test_extract_refs_is_first_seen_order_and_deduped(writer):
    known = {"a", "b"}
    assert writer.extract_refs("#b then #a then #b", known, "self") == ["b", "a"]


def test_today_is_pinnable_for_tests(writer):
    assert writer.today({"AGD_MEMORY_TODAY": "2020-01-01"}) == "2020-01-01"


# -- CLI, exercised against the real binary -----------------------------------

@requires_agd
def test_cli_bootstraps_memory_and_writes(cli, tmp_path, capsys):
    body = tmp_path / "b.txt"
    body.write_text("the body")
    assert cli.main(["x-project", "a-fact", "--desc", "d", "--content-file", str(body)]) == 0
    assert "added #a-fact" in capsys.readouterr().out


@requires_agd
def test_cli_rejects_fence_corrupting_body_with_exit_1(cli, tmp_path, capsys):
    body = tmp_path / "b.txt"
    body.write_text("a\n~~~\nb")
    assert cli.main(["x-project", "a-fact", "--content-file", str(body)]) == 1
    assert "save rejected" in capsys.readouterr().err


@requires_agd
def test_cli_rejects_invalid_id_with_exit_1(cli, capsys):
    assert cli.main(["x-project", "bad.id", "--content", "x"]) == 1
    assert "invalid id" in capsys.readouterr().err


@requires_agd
def test_cli_missing_content_file_exits_1(cli, tmp_path, capsys):
    missing = str(tmp_path / "nope.txt")
    assert cli.main(["x-project", "a-fact", "--content-file", missing]) == 1
    assert "cannot read" in capsys.readouterr().err


@requires_agd
def test_cli_global_scope_writes_to_the_global_layer(cli, tmp_path, capsys):
    assert cli.main(
        ["x-user", "a-pref", "--content", "x", "--scope", "global"]
    ) == 0
    assert "global.agd" in capsys.readouterr().out
    assert (tmp_path / "home" / ".claude" / "memory" / "global.agd").is_file()


@requires_agd
def test_cli_supersede_flags_the_replaced_block(cli, capsys):
    cli.main(["x-project", "old-way", "--desc", "old", "--content", "x"])
    capsys.readouterr()
    assert cli.main([
        "x-project", "new-way", "--content", "replaces #old-way",
        "--supersedes", "old-way",
    ]) == 0
    assert "superseded #old-way" in capsys.readouterr().out
