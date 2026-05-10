#!/usr/bin/env python3
"""Ingest a Python repo into an AGD memory file.

For each .py file under repo_root, emit one block per top-level
function/class (and one per method of each class). Each block:

    @x-symbol desc="<signature>" refs="#<callee_id>,#<callee_id>" [#<id>]
    ~~~
    <full source of the function/class>
    ~~~

`id` is `<file_slug>::<symbol_path>` where `file_slug` replaces / and
. with - and `symbol_path` is dotted (Class.method). The leading
TOC-anchor blocks are emitted as h2 headings per file so the agent can
filter by file via TOC scan.

Refs resolution is *intra-module only*: a Call to bare name X is
linked to a same-module symbol named X if one exists; cross-module
calls are not resolved (would require import graph). This is a
deliberate v0 simplification — captures most fan-in within tightly
coupled modules without false positives across the project.

Output: one .agd file at out_path.
"""
from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


SLUG_RE = re.compile(r"[^A-Za-z0-9_]+")


def slug(s: str) -> str:
    s = SLUG_RE.sub("-", s).strip("-")
    return s.lower()[:80]


@dataclass
class Symbol:
    sid: str            # block id
    name: str           # bare symbol name (last component)
    qual: str           # dotted (Class.method) or bare
    file_path: str
    signature: str
    body: str
    lineno: int = 0     # 1-indexed start line in the original file
    endline: int = 0    # 1-indexed end line, inclusive
    callees: list[str] = field(default_factory=list)  # bare names of called functions


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        try:
            return f"{kw} {node.name}({ast.unparse(node.args)})"
        except Exception:
            return f"{kw} {node.name}(...)"
    if isinstance(node, ast.ClassDef):
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("?")
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    return ""


def _called_names(node: ast.AST) -> list[str]:
    """Bare name of every Call. Skips method calls (foo.bar()) since
    intra-module resolution is by name, not method. Includes self.bar
    by stripping the leading self/cls."""
    out: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        f = child.func
        if isinstance(f, ast.Name):
            out.append(f.id)
        elif isinstance(f, ast.Attribute):
            # self.foo() -> foo ; cls.foo() -> foo ; mod.foo() -> foo
            if isinstance(f.value, ast.Name) and f.value.id in {"self", "cls"}:
                out.append(f.attr)
            else:
                out.append(f.attr)
    return out


def _src_for(node: ast.AST, source: str, lines: list[str]) -> str:
    s = getattr(node, "lineno", 1) - 1
    e = getattr(node, "end_lineno", s + 1)
    return "\n".join(lines[s:e])


def _span(node: ast.AST) -> tuple[int, int]:
    s = getattr(node, "lineno", 1)
    e = getattr(node, "end_lineno", s)
    return s, e


def _extract_symbols(file_rel: str, source: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    syms: list[Symbol] = []
    file_slug = slug(file_rel)

    used: set[str] = set()

    def unique(base: str) -> str:
        if base not in used:
            used.add(base); return base
        i = 2
        while f"{base}-{i}" in used:
            i += 1
        used.add(f"{base}-{i}")
        return f"{base}-{i}"

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sid = unique(f"{file_slug}--f-{slug(node.name)}")
            s, e = _span(node)
            syms.append(Symbol(
                sid=sid,
                name=node.name,
                qual=node.name,
                file_path=file_rel,
                signature=_signature(node),
                body=_src_for(node, source, lines),
                lineno=s,
                endline=e,
                callees=_called_names(node),
            ))
        elif isinstance(node, ast.ClassDef):
            cls_sid = unique(f"{file_slug}--c-{slug(node.name)}")
            s, e = _span(node)
            syms.append(Symbol(
                sid=cls_sid,
                name=node.name,
                qual=node.name,
                file_path=file_rel,
                signature=_signature(node),
                body=_src_for(node, source, lines),
                lineno=s,
                endline=e,
                callees=_called_names(node),
            ))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    msid = unique(f"{file_slug}--c-{slug(node.name)}--m-{slug(sub.name)}")
                    ms, me = _span(sub)
                    syms.append(Symbol(
                        sid=msid,
                        name=sub.name,
                        qual=f"{node.name}.{sub.name}",
                        file_path=file_rel,
                        signature=_signature(sub),
                        body=_src_for(sub, source, lines),
                        lineno=ms,
                        endline=me,
                        callees=_called_names(sub),
                    ))
    return syms


def _resolve_refs(symbols: list[Symbol]) -> list[Symbol]:
    """For each symbol, map callee bare names to symbol ids in the SAME file."""
    by_file: dict[str, dict[str, str]] = {}
    for s in symbols:
        by_file.setdefault(s.file_path, {})[s.name] = s.sid

    for s in symbols:
        same_file = by_file.get(s.file_path, {})
        refs = []
        seen: set[str] = set()
        for cn in s.callees:
            sid = same_file.get(cn)
            if sid and sid != s.sid and sid not in seen:
                refs.append(sid)
                seen.add(sid)
        s.callees = refs  # repurpose: now stores resolved callee sids
    return symbols


def _walk_py(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.py"):
        if any(seg in {".git", "__pycache__", ".tox", "node_modules", "build", "dist", ".eggs"} for seg in p.parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def _format_block(s: Symbol) -> str:
    desc_safe = s.signature.replace('"', "'")[:200]
    refs_attr = f' refs="{",".join("#"+r for r in s.callees)}"' if s.callees else ""
    return (
        f'@x-symbol desc="{desc_safe}" file="{s.file_path}" qual="{s.qual}" '
        f'lineno={s.lineno} endline={s.endline}{refs_attr} [#{s.sid}]\n'
        f"~~~\n"
        f"{s.body}\n"
        f"~~~\n"
    )


def ingest(repo_root: Path, out_path: Path) -> dict:
    py_files = _walk_py(repo_root)
    all_syms: list[Symbol] = []
    skipped = 0
    for p in py_files:
        try:
            src = p.read_text(errors="replace")
        except OSError:
            skipped += 1
            continue
        rel = str(p.relative_to(repo_root))
        all_syms.extend(_extract_symbols(rel, src))
    _resolve_refs(all_syms)

    # Build doc in AGD syntax: @meta + @h1 + per-file @h2 + @x-symbol blocks
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_file: dict[str, list[Symbol]] = {}
    for s in all_syms:
        by_file.setdefault(s.file_path, []).append(s)

    chunks: list[str] = []
    chunks.append('@meta format=agd generated=ingest source=python-repo [#meta]\n\n')
    chunks.append('@h1 Repo memory [#root]\n\n')
    for file_path in sorted(by_file.keys()):
        h2_id = "file--" + slug(file_path)
        # @h2 takes inline text, no fence
        safe_title = file_path.replace("\n", " ")
        chunks.append(f'@h2 {safe_title} [#{h2_id}]\n\n')
        for s in by_file[file_path]:
            chunks.append(_format_block(s) + "\n")
    out_path.write_text("".join(chunks))

    edge_count = sum(len(s.callees) for s in all_syms)
    return {
        "files_indexed": len(by_file),
        "symbols": len(all_syms),
        "edges": edge_count,
        "files_skipped": skipped,
        "out_path": str(out_path),
        "out_size_bytes": out_path.stat().st_size,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    import json
    print(json.dumps(ingest(args.repo, args.out), indent=2))


if __name__ == "__main__":
    main()
