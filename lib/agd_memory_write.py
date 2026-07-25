"""The one place a memory block gets written.

Two callers need this: the MCP server (interactive saves) and the
SessionEnd distiller (headless capture). They used to have separate
implementations — the distiller shipped its own `agd_mem_save.py` — which
is how a write path acquires two different sets of guard rails and only
one of them gets the bug fix. Everything that decides *what lands on
disk* lives here; the callers only translate their own argument shapes
into `save_block`.

Deliberately free of any `mcp` import so a CLI can use it without the
server's dependencies.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

__all__ = [
    "SaveRejected",
    "bootstrap_memory_file",
    "reject_invalid_block",
    "reject_unfencable",
    "save_block",
    "toc_entries",
    "VALID_STATUS",
]

# `agd edit` does not validate identifiers or block tags on WRITE (exit 0),
# but the parser rejects them on every subsequent READ — one bad save makes
# the whole memory file unreadable. Same grammar as agd's is_valid_ident.
_VALID_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
_VALID_KIND = re.compile(r"x-[A-Za-z_][A-Za-z0-9_-]*\Z")

# Blocks cite each other in prose — `Rif. #catalogo-unico, #produzione-swap`
# — which `agd backlinks` and `--follow-refs` cannot see. Measured on the
# real corpus: 14 blocks carried a `refs=` attribute while 138 prose
# citations sat inside bodies. Lifting them makes the graph traversable.
_REF_IN_BODY = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")

# Lifecycle vocabulary. Dates used to live in prose — `desc="FATTO
# 2026-06-25: dedup Cod+Extra"` — 322 such strings across the corpus,
# none of them queryable.
VALID_STATUS = ("open", "done", "superseded")
_TODAY_ENV = "AGD_MEMORY_TODAY"  # tests pin this; production reads the clock

SCAFFOLD = """\
@meta format=agd version=1 [#meta]

@h1 Memoria di sessione [#root]

@h2 User [#h-user]

@h2 Feedback [#h-feedback]

@h2 Project [#h-project]

@h2 Reference [#h-reference]
"""


class SaveRejected(Exception):
    """The block would corrupt the file, or name something invalid."""


def today(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return env.get(_TODAY_ENV) or _dt.date.today().isoformat()


def reject_invalid_block(kind: str, block_id: str) -> str | None:
    """Return None if `kind` and `block_id` are safe to write, else a reason.
    Offers a sanitized id so the caller can retry without guessing."""
    if not _VALID_ID.match(block_id):
        suggestion = re.sub(r"[^A-Za-z0-9_-]+", "-", block_id).strip("-")
        if not suggestion or not re.match(r"[A-Za-z_]", suggestion):
            suggestion = "x-" + (suggestion or "entry")
        return (
            f"invalid id `{block_id}`: must match [A-Za-z_][A-Za-z0-9_-]* "
            f"(no dots or other punctuation). Retry with e.g. `{suggestion}`. "
            "Nothing was written."
        )
    if not _VALID_KIND.match(kind):
        return (
            f"invalid kind `{kind}`: must be `x-` followed by an identifier "
            "(e.g. x-project, x-user, x-feedback, x-reference). "
            "Nothing was written."
        )
    return None


def reject_unfencable(content: str) -> str | None:
    """Return None if `content` is safe to embed in a `~~~`-fenced AGD body.
    Otherwise return a human-readable reason. The AGD format closes a fence
    at the first line equal exactly to `~~~`, so any standalone `~~~` line
    inside the body would corrupt the file on round-trip."""
    for i, line in enumerate(content.split("\n"), start=1):
        if line == "~~~":
            return (
                f"content contains a standalone `~~~` line (line {i}) which "
                "would close the fence prematurely and corrupt the file. "
                "Reword the body so no line is exactly `~~~` (e.g. add "
                "leading whitespace, or use a different separator)."
            )
    return None


def parse_ref_attr(raw: object) -> list[str]:
    """Split a `refs="#a,#b"` attribute into bare ids."""
    if not isinstance(raw, str):
        return []
    return [r.strip().lstrip("#") for r in raw.split(",") if r.strip().lstrip("#")]


def extract_refs(content: str, known_ids: set[str], self_id: str) -> list[str]:
    """Return the ids this body cites, in first-seen order.

    Only ids that resolve to a block in the same file become edges. That
    filter is what keeps the extraction safe: `#1` issue numbers, `#fff`
    colour literals and markdown headings never resolve, so they are
    silently ignored rather than producing dangling edges.
    """
    out: list[str] = []
    for m in _REF_IN_BODY.finditer(content):
        rid = m.group(1)
        if rid != self_id and rid in known_ids and rid not in out:
            out.append(rid)
    return out


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """Hold an exclusive flock on a sibling lockfile for the duration of
    the read-modify-write cycle. Prevents lost updates when several
    processes (one per Claude Code session, plus the distiller) write at
    once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lockfile = path.with_suffix(path.suffix + ".lock")
    fd = os.open(str(lockfile), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def bootstrap_memory_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCAFFOLD)
    return path


def _run(agd_bin: Path, args: list[str]) -> str:
    return subprocess.run(
        [str(agd_bin), *args], text=True, check=True, capture_output=True
    ).stdout


def toc_entries(
    agd_bin: Path,
    mem: Path,
    kind: str | None = None,
    status: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Project the document into a compact TOC.

    Built from `agd parse` rather than `agd ids` because the latter only
    surfaces id/kind/desc — the lifecycle attributes would be invisible
    exactly where they are most useful. `status` and `updated` are
    emitted only when set, so entries stay as cheap as before for blocks
    that carry neither. `since` filters on `updated` (ISO dates compare
    correctly as strings).
    """
    doc = json.loads(_run(agd_bin, ["parse", str(mem), "--json"]))
    entries: list[dict] = []
    for b in doc.get("blocks", []) if isinstance(doc, dict) else []:
        bid = b.get("id")
        if not bid:
            continue
        bkind = b.get("kind", "") or ""
        if kind and bkind != kind:
            continue
        attrs = b.get("attrs") or {}
        if status and attrs.get("status") != status:
            continue
        if since and (attrs.get("updated") or "") < since:
            continue
        entry = {"id": bid, "kind": bkind}
        for field in ("desc", "status", "updated"):
            if attrs.get(field):
                entry[field] = attrs[field]
        entries.append(entry)
    return entries


def _mark_superseded(agd_bin: Path, mem: Path, ids: list[str]) -> list[str]:
    """Flag each block in `ids` as superseded, preserving everything else.

    Called after a successful write, inside the same lock. Best-effort:
    the new block is already on disk, so a failure here must not turn a
    completed save into a reported error.
    """
    marked: list[str] = []
    for sid in ids:
        try:
            raw = json.loads(_run(agd_bin, ["get", str(mem), f"#{sid}", "--json"]))
            block = raw[0] if isinstance(raw, list) and raw else raw
            if not isinstance(block, dict):
                continue
            attrs = dict(block.get("attrs") or {})
            if attrs.get("status") == "superseded":
                continue
            attrs["status"] = "superseded"
            attrs["updated"] = today()
            _run(agd_bin, [
                "edit", str(mem), "-i", "--op", json.dumps({
                    "op": "replace",
                    "id": sid,
                    "with": {
                        "kind": block.get("kind", ""),
                        "attrs": attrs,
                        "id": sid,
                        "content": block.get("content")
                        or {"type": "fenced", "value": ""},
                    },
                }),
            ])
            marked.append(sid)
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            continue
    return marked


def save_block(
    agd_bin: Path,
    mem: Path,
    kind: str,
    block_id: str,
    content: str,
    *,
    desc: str | None = None,
    refs: list[str] | None = None,
    status: str | None = None,
    supersedes: list[str] | None = None,
) -> str:
    """Add or replace one block. Returns a one-line human-readable result.

    Raises `SaveRejected` when the input would corrupt the file, and
    `subprocess.CalledProcessError` when `agd edit` itself fails.
    """
    block_id = block_id.lstrip("#")
    reason = reject_invalid_block(kind, block_id) or reject_unfencable(content)
    if reason is not None:
        raise SaveRejected(reason)

    attrs: dict[str, str] = {}
    if desc:
        # A literal newline in a quoted attribute value is written as-is
        # by `agd edit` and breaks the parse on read ("unterminated
        # quoted value") — collapse to spaces.
        attrs["desc"] = re.sub(r"\s*\n\s*", " ", desc).strip()

    explicit_refs = [str(r).lstrip("#") for r in (refs or [])]

    with exclusive_lock(mem):
        if not mem.is_file():
            bootstrap_memory_file(mem)
        existing_ids = [
            line.split("\t", 1)[0]
            for line in _run(agd_bin, ["ids", str(mem)]).strip().splitlines()
        ]
        known_ids = set(existing_ids)

        old_attrs: dict = {}
        if block_id in existing_ids:
            try:
                raw = json.loads(
                    _run(agd_bin, ["get", str(mem), f"#{block_id}", "--json"])
                )
                existing = raw[0] if isinstance(raw, list) and raw else raw
                old_attrs = (existing or {}).get("attrs") or {}
            except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
                old_attrs = {}

        if not desc and old_attrs.get("desc"):
            attrs["desc"] = old_attrs["desc"]

        # Lifecycle. `created` is written once and carried forward verbatim;
        # `updated` moves on every write.
        stamp = today()
        attrs["created"] = old_attrs.get("created") or stamp
        attrs["updated"] = stamp

        effective_status = status or old_attrs.get("status")
        if effective_status in VALID_STATUS:
            attrs["status"] = effective_status

        sup = [str(s).lstrip("#") for s in (supersedes or [])] or parse_ref_attr(
            old_attrs.get("supersedes")
        )
        sup = [s for s in sup if s in known_ids and s != block_id]
        if sup:
            attrs["supersedes"] = ",".join(f"#{s}" for s in sup)

        # Edges: explicit argument first, then the ones the body cites.
        # Unknown targets are dropped — a dangling edge breaks `agd ref`.
        edges = [r for r in explicit_refs if r in known_ids and r != block_id]
        for r in extract_refs(content, known_ids, block_id):
            if r not in edges:
                edges.append(r)
        if not explicit_refs:
            # Same rule as `desc`: a caller that says nothing about refs
            # must not silently lose edges recorded earlier.
            for r in parse_ref_attr(old_attrs.get("refs")):
                if r in known_ids and r != block_id and r not in edges:
                    edges.append(r)
        if edges:
            attrs["refs"] = ",".join(f"#{r}" for r in edges)

        block = {
            "kind": kind,
            "attrs": attrs,
            "id": block_id,
            "content": {"type": "fenced", "value": content},
        }

        if block_id in existing_ids:
            op = {"op": "replace", "id": block_id, "with": block}
            verb = "updated"
        else:
            heading_id = f"h-{kind.removeprefix('x-')}"
            anchor = heading_id if heading_id in existing_ids else "root"
            op = {"op": "insert_after", "id": anchor, "block": block}
            verb = "added"

        _run(agd_bin, ["edit", str(mem), "--op", json.dumps(op), "-i"])
        marked = _mark_superseded(agd_bin, mem, sup)

    note = (
        f", superseded {', '.join('#' + m for m in marked)}" if marked else ""
    )
    return f"{verb} #{block_id} ({kind}) in {mem.name}{note}"
