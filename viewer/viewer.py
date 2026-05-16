#!/usr/bin/env python3
"""Local viewer for an AGD memory file.

Spins up a tiny http.server on a free port, serves a single-page UI that
shows the memory as a searchable list, a live graph (refs as edges), and
a body-rendering pane. Opens the browser automatically.

Usage:
    python3 viewer.py [path/to/memory.agd]

If the path is omitted, it follows the same convention as the MCP server:
    ~/.claude/projects/<sanitized-cwd>/memory/memory.agd
or honors the AGD_MEMORY_FILE environment variable.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
AGD_BIN = Path(os.environ.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))
PROJECTS_DIR = Path.home() / ".claude" / "projects"
SLUG_RE = re.compile(r"^[A-Za-z0-9._\-]+$")  # nessun `/`, no traversal


def memory_path(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    if env := os.environ.get("AGD_MEMORY_FILE"):
        return Path(env).expanduser().resolve()
    cwd = Path(os.environ.get("AGD_MEMORY_PROJECT_CWD", os.getcwd())).resolve()
    sanitized = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / "memory" / "memory.agd"


def slug_to_display(slug: str) -> str:
    """Sanitized cwd → path leggibile. Convenzione MCP: cwd.replace('/', '-')."""
    return "/" + slug.lstrip("-").replace("-", "/")


def list_projects() -> list[dict]:
    """Scansiona ~/.claude/projects/*/memory/memory.agd e ritorna metadata."""
    if not PROJECTS_DIR.is_dir():
        return []
    out = []
    for child in sorted(PROJECTS_DIR.iterdir()):
        if not child.is_dir() or not SLUG_RE.match(child.name):
            continue
        mem = child / "memory" / "memory.agd"
        if not mem.is_file():
            continue
        try:
            st = mem.stat()
        except OSError:
            continue
        out.append({
            "slug": child.name,
            "display": slug_to_display(child.name),
            "path": str(mem),
            "bytes": st.st_size,
            "mtime": st.st_mtime,
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def resolve_memory(slug: str | None, fallback: Path) -> Path | None:
    """Risolve slug → path memoria, con difesa anti path-traversal."""
    if not slug:
        return fallback
    if not SLUG_RE.match(slug):
        return None
    candidate = (PROJECTS_DIR / slug / "memory" / "memory.agd").resolve()
    # difesa: il path risolto deve restare sotto PROJECTS_DIR
    try:
        candidate.relative_to(PROJECTS_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def parse_block(b: dict) -> dict:
    attrs = b.get("attrs") or {}
    refs_raw = attrs.get("refs")
    refs: list[str] = []
    if isinstance(refs_raw, str):
        for r in refs_raw.split(","):
            r = r.strip().lstrip("#")
            if r:
                refs.append(r)
    content = b.get("content") or {}
    ctype = content.get("type")
    body = ""
    if ctype == "fenced":
        body = content.get("value") or ""
    elif ctype == "inline":
        parts = content.get("value") or []
        body = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    elif ctype == "items":
        rows = content.get("value") or []
        lines = []
        for row in rows:
            text = "".join(p.get("text", "") for p in row if isinstance(p, dict))
            lines.append(f"- {text}")
        body = "\n".join(lines)
    return {
        "id": b.get("id"),
        "kind": b.get("kind"),
        "desc": attrs.get("desc"),
        "attrs": {k: v for k, v in attrs.items() if k != "desc"},
        "body": body,
        "refs": refs,
    }


def build_payload(mem: Path) -> dict:
    if not mem.is_file():
        return {"error": f"memory file not found: {mem}"}
    raw = subprocess.run(
        [str(AGD_BIN), "parse", str(mem), "--json"],
        capture_output=True,
        text=True,
    )
    if raw.returncode != 0:
        return {"error": f"agd parse failed: {raw.stderr.strip()}"}
    try:
        doc = json.loads(raw.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"could not decode agd parse output: {e}"}

    blocks = []
    for b in doc.get("blocks") or []:
        if not b.get("id"):
            continue
        blocks.append(parse_block(b))

    # Backlinks
    by_id = {b["id"]: b for b in blocks}
    backlinks: dict[str, list[str]] = {bid: [] for bid in by_id}
    for b in blocks:
        for tgt in b["refs"]:
            if tgt in backlinks:
                backlinks[tgt].append(b["id"])
    for b in blocks:
        b["backlinks"] = backlinks.get(b["id"], [])

    # Stats
    kinds: dict[str, int] = {}
    for b in blocks:
        k = b["kind"] or "?"
        kinds[k] = kinds.get(k, 0) + 1
    edges = sum(len(b["refs"]) for b in blocks)
    return {
        "path": str(mem),
        "stats": {
            "blocks": len(blocks),
            "kinds": kinds,
            "edges": edges,
            "bytes": mem.stat().st_size,
        },
        "blocks": blocks,
    }


class Handler(BaseHTTPRequestHandler):
    mem: Path = Path()  # set per-instance via factory
    initial_slug: str | None = None

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/" or path == "/index.html":
            try:
                body = INDEX_HTML.read_bytes()
            except OSError as e:
                self._send(500, str(e).encode(), "text/plain")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/projects.json":
            payload = {
                "initial_slug": self.initial_slug,
                "initial_path": str(self.mem),
                "projects": list_projects(),
            }
            body = json.dumps(payload).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/memory.json":
            slug = (qs.get("project") or [None])[0]
            resolved = resolve_memory(slug, self.mem)
            if resolved is None:
                self._send(404, json.dumps({"error": f"project not found: {slug}"}).encode(),
                           "application/json; charset=utf-8")
                return
            payload = build_payload(resolved)
            body = json.dumps(payload).encode()
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):  # silence default access log
        sys.stderr.write(f"[viewer] {self.address_string()} {fmt % args}\n")


def find_free_port(preferred: int = 8765) -> int:
    for p in [preferred, *range(8766, 8800)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    mem = memory_path(arg)
    if not mem.is_file():
        print(f"agd-memory viewer: file not found: {mem}", file=sys.stderr)
        print("Pass a path explicitly or set AGD_MEMORY_FILE.", file=sys.stderr)
        return 2

    port = find_free_port()

    # initial_slug: se il path iniziale è dentro ~/.claude/projects/<slug>/memory/memory.agd,
    # ricavalo per pre-selezione nel dropdown.
    initial_slug = None
    try:
        rel = mem.relative_to(PROJECTS_DIR.resolve())
        parts = rel.parts
        if len(parts) >= 1:
            initial_slug = parts[0]
    except (ValueError, OSError):
        pass

    HandlerClass = type("BoundHandler", (Handler,),
                        {"mem": mem, "initial_slug": initial_slug})
    server = ThreadingHTTPServer(("127.0.0.1", port), HandlerClass)
    url = f"http://127.0.0.1:{port}/"

    print(f"agd-memory viewer", file=sys.stderr)
    print(f"  file: {mem}", file=sys.stderr)
    print(f"  url:  {url}", file=sys.stderr)
    print(f"  Ctrl+C to stop.", file=sys.stderr)

    threading.Thread(
        target=lambda: (time.sleep(0.4), webbrowser.open(url)),
        daemon=True,
    ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
