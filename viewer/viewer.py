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
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
AGD_BIN = Path(os.environ.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))


def memory_path(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    if env := os.environ.get("AGD_MEMORY_FILE"):
        return Path(env).expanduser().resolve()
    cwd = Path(os.environ.get("AGD_MEMORY_PROJECT_CWD", os.getcwd())).resolve()
    sanitized = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized / "memory" / "memory.agd"


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

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path == "/index.html":
            try:
                body = INDEX_HTML.read_bytes()
            except OSError as e:
                self._send(500, str(e).encode(), "text/plain")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path == "/memory.json":
            payload = build_payload(self.mem)
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

    HandlerClass = type("BoundHandler", (Handler,), {"mem": mem})
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
