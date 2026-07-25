"""Safety-net tests on the existing MCP server code.

Covers:
  * pure helpers `_reject_unfencable`, `memory_path`
  * the save path: dispatches the right `--op` JSON to `agd edit` for
    new/existing block ids, with/without explicit `desc`, preserves
    old desc when the caller omits it, surfaces failures cleanly.

The `fake_agd` fixture (see conftest.py) installs a test double on
`$AGD_BIN` and logs each invocation's argv so we can assert on the
`--op` JSON passed to `edit`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


pytest.importorskip("mcp")


@pytest.fixture
def server_module(fake_agd, monkeypatch):
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.syspath_prepend(str(repo_root / "mcp_servers"))
    import importlib

    if "server" in list(__import__("sys").modules):
        import sys as _sys
        del _sys.modules["server"]
    import server  # noqa: WPS433 — import after path setup

    monkeypatch.setattr(server, "AGD_BIN", fake_agd.bin)
    return server


def _edit_op_from_log(fake_agd) -> dict:
    edit_calls = fake_agd.calls("edit")
    assert edit_calls, "expected an edit invocation"
    argv = edit_calls[-1]
    assert "--op" in argv, f"no --op flag in {argv!r}"
    op_json = argv[argv.index("--op") + 1]
    return json.loads(op_json)


# -- _reject_unfencable ------------------------------------------------------

def test_reject_unfencable_rejects_standalone_tilde_line(server_module):
    reason = server_module._reject_unfencable("ok\n~~~\nend")
    assert reason is not None
    assert "~~~" in reason


def test_reject_unfencable_accepts_indented_tilde(server_module):
    assert server_module._reject_unfencable("ok\n  ~~~\nend") is None


def test_reject_unfencable_accepts_inline_tilde_inside_line(server_module):
    assert server_module._reject_unfencable("see ~~~ inside") is None


# -- memory_path -------------------------------------------------------------

def test_memory_path_uses_AGD_MEMORY_FILE_override(server_module, monkeypatch, tmp_path):
    target = tmp_path / "custom.agd"
    monkeypatch.setenv("AGD_MEMORY_FILE", str(target))
    assert server_module.memory_path() == target


def test_memory_path_falls_back_to_AGD_MEMORY_PROJECT_CWD_sanitisation(
    server_module, monkeypatch, tmp_path
):
    monkeypatch.delenv("AGD_MEMORY_FILE", raising=False)
    # Use a real directory under tmp_path to avoid symlink resolution
    # surprises (e.g. /tmp → /private/tmp on macOS).
    project = tmp_path / "example" / "project"
    project.mkdir(parents=True)
    monkeypatch.setenv("AGD_MEMORY_PROJECT_CWD", str(project))
    expected_dir_name = str(project.resolve()).replace("/", "-")
    p = server_module.memory_path()
    assert p.parts[-3] == expected_dir_name
    assert p.name == "memory.agd"


def test_memory_path_sanitises_slashes_to_dashes(
    server_module, monkeypatch, tmp_path
):
    monkeypatch.delenv("AGD_MEMORY_FILE", raising=False)
    nested = tmp_path / "alice" / "work"
    nested.mkdir(parents=True)
    monkeypatch.setenv("AGD_MEMORY_PROJECT_CWD", str(nested))
    p = server_module.memory_path()
    assert "/" not in p.parts[-3]
    assert "alice-work" in p.parts[-3]


# -- save path ---------------------------------------------------------------

def _call_save(server_module, **kwargs) -> list:
    return asyncio.run(server_module._call_tool("agd_memory_save", kwargs))


def test_save_inserts_after_h_kind_for_new_id(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "h-feedback", "kind": "h2", "body": "Feedback"},
    ]})
    out = _call_save(
        server_module,
        kind="x-feedback",
        id="feedback-new",
        content="body",
    )
    assert out and "added" in out[0].text
    op = _edit_op_from_log(fake_agd)
    assert op["op"] == "insert_after"
    assert op["id"] == "h-feedback"
    assert op["block"]["id"] == "feedback-new"
    assert op["block"]["kind"] == "x-feedback"


def test_save_inserts_after_root_when_h_kind_missing(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "root", "kind": "h1", "body": "Memoria"},
    ]})
    _call_save(server_module, kind="x-user", id="user-foo", content="body")
    op = _edit_op_from_log(fake_agd)
    assert op["op"] == "insert_after"
    assert op["id"] == "root"


def test_save_replace_op_for_existing_id(server_module, fake_agd, tmp_memory):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-foo", "kind": "x-user", "desc": "old", "body": "old"},
    ]})
    out = _call_save(
        server_module,
        kind="x-user",
        id="user-foo",
        content="new body",
        desc="new",
    )
    assert out and "updated" in out[0].text
    op = _edit_op_from_log(fake_agd)
    assert op["op"] == "replace"
    assert op["id"] == "user-foo"
    assert op["with"]["content"]["value"] == "new body"
    assert op["with"]["attrs"]["desc"] == "new"


def test_save_stamps_created_and_updated(
    server_module, fake_agd, tmp_memory, monkeypatch
):
    monkeypatch.setenv("AGD_MEMORY_TODAY", "2026-07-25")
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
    ]})
    _call_save(server_module, kind="x-project", id="proj", content="body")
    attrs = _edit_op_from_log(fake_agd)["block"]["attrs"]
    assert attrs["created"] == "2026-07-25"
    assert attrs["updated"] == "2026-07-25"


def test_save_carries_created_forward_and_moves_updated(
    server_module, fake_agd, tmp_memory, monkeypatch
):
    """`created` is written once; only `updated` tracks later edits."""
    fake_agd.write_fixture({"blocks": [
        {"id": "proj", "kind": "x-project", "body": "x",
         "attrs": {"created": "2026-01-01", "updated": "2026-01-01"}},
    ]})
    monkeypatch.setenv("AGD_MEMORY_TODAY", "2026-07-25")
    _call_save(server_module, kind="x-project", id="proj", content="new body")
    attrs = _edit_op_from_log(fake_agd)["with"]["attrs"]
    assert attrs["created"] == "2026-01-01"
    assert attrs["updated"] == "2026-07-25"


def test_save_rejects_unknown_status_silently(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
    ]})
    _call_save(
        server_module, kind="x-project", id="proj", content="b", status="bogus",
    )
    assert "status" not in _edit_op_from_log(fake_agd)["block"]["attrs"]


def test_save_flags_the_blocks_it_supersedes(
    server_module, fake_agd, tmp_memory, monkeypatch
):
    monkeypatch.setenv("AGD_MEMORY_TODAY", "2026-07-25")
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
        {"id": "old", "kind": "x-project", "desc": "the old truth", "body": "x"},
    ]})
    out = _call_save(
        server_module, kind="x-project", id="new", content="b",
        supersedes=["old"],
    )
    assert "superseded #old" in out[0].text
    ops = [
        json.loads(argv[argv.index("--op") + 1])
        for argv in fake_agd.calls("edit")
    ]
    patch = [o for o in ops if o.get("id") == "old"]
    assert patch and patch[0]["with"]["attrs"]["status"] == "superseded"
    # The superseded block keeps its content and description.
    assert patch[0]["with"]["attrs"]["desc"] == "the old truth"


def test_save_lifts_prose_citations_into_refs(
    server_module, fake_agd, tmp_memory
):
    """A body that says 'builds on #earlier' becomes a traversable edge."""
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
        {"id": "earlier", "kind": "x-project", "body": "x"},
    ]})
    _call_save(
        server_module,
        kind="x-project",
        id="later",
        content="Builds on #earlier, which set the schema.",
    )
    op = _edit_op_from_log(fake_agd)
    assert op["block"]["attrs"]["refs"] == "#earlier"


def test_save_ignores_citations_that_do_not_resolve(
    server_module, fake_agd, tmp_memory
):
    """`#1` issue numbers and `#fff` colours must not become dangling edges."""
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
    ]})
    _call_save(
        server_module,
        kind="x-project",
        id="proj",
        content="Fixes #1 and #42; accent colour #fff; see #nonexistent-block.",
    )
    op = _edit_op_from_log(fake_agd)
    assert "refs" not in op["block"]["attrs"]


def test_save_never_creates_a_self_edge(server_module, fake_agd, tmp_memory):
    fake_agd.write_fixture({"blocks": [
        {"id": "h-project", "kind": "h2", "body": "Project"},
        {"id": "proj", "kind": "x-project", "body": "x"},
    ]})
    _call_save(
        server_module, kind="x-project", id="proj", content="See #proj itself.",
    )
    op = _edit_op_from_log(fake_agd)
    assert "refs" not in op["with"]["attrs"]


def test_save_preserves_existing_refs_when_caller_omits_them(
    server_module, fake_agd, tmp_memory
):
    """Same rule as desc: silence must not drop edges recorded earlier."""
    fake_agd.write_fixture({"blocks": [
        {"id": "other", "kind": "x-project", "body": "x"},
        {"id": "proj", "kind": "x-project", "refs": ["other"], "body": "x"},
    ]})
    _call_save(server_module, kind="x-project", id="proj", content="new body")
    op = _edit_op_from_log(fake_agd)
    assert op["with"]["attrs"]["refs"] == "#other"


def test_save_explicit_refs_replace_preserved_ones(
    server_module, fake_agd, tmp_memory
):
    """Passing refs= is how an edge gets removed."""
    fake_agd.write_fixture({"blocks": [
        {"id": "a", "kind": "x-project", "body": "x"},
        {"id": "b", "kind": "x-project", "body": "x"},
        {"id": "proj", "kind": "x-project", "refs": ["a"], "body": "x"},
    ]})
    _call_save(
        server_module, kind="x-project", id="proj", content="new", refs=["b"],
    )
    op = _edit_op_from_log(fake_agd)
    assert op["with"]["attrs"]["refs"] == "#b"


def test_save_preserves_existing_desc_when_caller_omits_it(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-foo", "kind": "x-user", "desc": "old desc", "body": "x"},
    ]})
    _call_save(
        server_module,
        kind="x-user",
        id="user-foo",
        content="new body",
        # no desc=
    )
    op = _edit_op_from_log(fake_agd)
    assert op["op"] == "replace"
    assert op["with"]["attrs"].get("desc") == "old desc"


def test_save_overwrites_desc_when_caller_provides_one(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-foo", "kind": "x-user", "desc": "old desc", "body": "x"},
    ]})
    _call_save(
        server_module,
        kind="x-user",
        id="user-foo",
        content="new body",
        desc="brand new",
    )
    op = _edit_op_from_log(fake_agd)
    assert op["with"]["attrs"]["desc"] == "brand new"


def test_save_returns_friendly_error_on_agd_failure(
    server_module, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [], "edit_fails": True})
    out = _call_save(server_module, kind="x-user", id="x", content="body")
    assert out and "save failed" in out[0].text


# -- _reject_invalid_block ---------------------------------------------------
# Born from a real incident: a save with id `...-1.9.0` (dots) corrupted a
# live memory file. The guard shipped without tests — these pin it shut.

def test_reject_invalid_block_rejects_id_with_dots(server_module):
    reason = server_module._reject_invalid_block("x-project", "ctx-kernel-1.9.0")
    assert reason is not None
    assert "invalid id" in reason
    # offers a sanitized, retryable suggestion (dots → dashes)
    assert "ctx-kernel-1-9-0" in reason


def test_reject_invalid_block_rejects_non_x_kind(server_module):
    reason = server_module._reject_invalid_block("project", "ok-id")
    assert reason is not None
    assert "invalid kind" in reason


def test_reject_invalid_block_accepts_valid(server_module):
    assert server_module._reject_invalid_block("x-user", "some_id-1") is None


def test_save_collapses_newline_in_desc(server_module, fake_agd, tmp_memory):
    # A literal newline in a quoted attr value breaks the parse on read; the
    # save path must collapse it to a space, not write it through.
    fake_agd.write_fixture({"blocks": [{"id": "root", "kind": "h1", "body": "M"}]})
    _call_save(
        server_module,
        kind="x-project",
        id="note-nl",
        content="body",
        desc="line one\nline two",
    )
    op = _edit_op_from_log(fake_agd)
    desc = (op["block"]["attrs"] or {}).get("desc")
    assert desc == "line one line two"
    assert "\n" not in desc


# -- agd_memory_get graph flags (C3) -----------------------------------------

def _call_get(server_module, **kwargs) -> None:
    asyncio.run(server_module._call_tool("agd_memory_get", kwargs))


def test_get_forwards_graph_flags(server_module, fake_agd, tmp_memory):
    _call_get(server_module, ids=["a"], with_backlinks=True, follow_refs=True, depth=2)
    argv = fake_agd.calls("get")[-1]
    assert "--with-backlinks" in argv
    assert "--follow-refs" in argv
    assert "--depth" in argv and argv[argv.index("--depth") + 1] == "2"


def test_get_omits_graph_flags_by_default(server_module, fake_agd, tmp_memory):
    _call_get(server_module, ids=["a"])
    argv = fake_agd.calls("get")[-1]
    assert "--with-backlinks" not in argv
    assert "--follow-refs" not in argv
    assert "--depth" not in argv
