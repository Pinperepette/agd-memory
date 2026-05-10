#!/usr/bin/env python3
"""AGD-graph adapter: TOC + selective fetch + backlinks.

Uses a pre-ingested AGD corpus of the repository (one block per
function/class, refs= for intra-module call edges). The model is
given four tools mirroring the agd CLI:

- agd_toc          : list of (id, kind, desc) — cheap entry point
- agd_search       : substring search across block bodies
- agd_get          : fetch N blocks at once (batched)
- agd_backlinks    : block id -> who refs= it

Compared to grep-agent the *retrieval* is the same number of round
trips, but each round trip ships a precise block (function body)
instead of a file slice. RAF should drop sharply on tasks where the
fix is localised to one function whose callers/callees are linkable.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import tiktoken

from .base import RetrievalAdapter, AdapterResult, PATCH_INSTRUCTIONS, extract_patch

ENC = tiktoken.get_encoding("cl100k_base")
MAX_TURNS = 25
TOOL_OUTPUT_CAP = 6000


SYSTEM = """You are a senior Python engineer fixing a real bug. You have an addressable memory of the repository: every function and class is stored as an AGD block with a stable id, attributes (file, qual, lineno, endline), and intra-module call edges as `refs="#callee_id"`.

ID CONVENTION:
  file--<file-slug>                                 -> heading for one source file
  <file-slug>--f-<funcname>                         -> top-level function
  <file-slug>--c-<classname>                        -> class
  <file-slug>--c-<classname>--m-<method>            -> method of a class
where <file-slug> = the path with `/` and `.` replaced by `-`, all lowercase.

Example: function `merge_setting` in `requests/sessions.py` has id `requests-sessions-py--f-merge_setting`.

Each block carries `lineno=` and `endline=` (1-indexed). Use these to build correct unified-diff hunk headers `@@ -<lineno>,<count> +<lineno>,<count> @@` against the original file.

Tools:
- agd_rank_symbols(query, top_k): BM25-rank symbols by relevance to a natural-language query. Returns top symbols with their file= attribute and desc. **USE THIS FIRST** when the problem statement does NOT mention a specific file path. The top result's file= tells you what to pass to agd_toc(file=...) next.
- agd_toc(file=PATH): TOC of one file's symbols (cheap and focused). Pass the suspected file path to scope the listing. Use agd_toc() (no file) only as a last resort.
- agd_search(query): exact substring search across block bodies (use only when you need a literal string match — for keywords prefer agd_rank_symbols).
- agd_get(ids, with_backlinks): fetch blocks by id; with_backlinks=True also includes callers.
- agd_backlinks(id): list ids that refs= the given block.
- read_window(path, start_line, end_line): read a raw slice of a source file. Use for the few neighbour lines you need to write a precise hunk header (the block body alone is not enough — you need surrounding context to anchor the diff).
- submit_patch(diff): submit the final unified diff. MANDATORY. Do NOT emit the diff as text.

Strategy:
1. Read the problem.
   - If it mentions a file path (e.g., `src/_pytest/skipping.py`): go straight to step 2.
   - If it does NOT mention a file path (e.g., "chained exception handling fails"): call agd_rank_symbols(query=KEY_TERMS) FIRST to find candidate symbols and their files. Then go to step 2 with the top result's file=.
2. agd_toc(file=PATH) on the suspected file to see its symbols.
3. agd_get on the suspect block; if needed, read_window for neighbour context.
4. submit_patch with the diff. The `file=` of the block is your `--- a/<path>`. Use `lineno=` and the body to compute hunk headers.

Be efficient: 2-5 tool calls plus submit_patch. Always submit_patch at the end — never let the loop time out."""


TOOLS = [
    {
        "name": "agd_rank_symbols",
        "description": "BM25-rank symbols by relevance to a natural-language query. Returns top symbols with their file=, desc, and id. Use FIRST when the problem statement does not mention a specific file path — the top result's file= tells you which file to scope subsequent agd_toc(file=...) calls to.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-form keywords from the problem statement. Multiple words are AND-ed via BM25 scoring."},
                "top_k": {"type": "integer", "default": 12, "description": "How many top symbols to return."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "agd_toc",
        "description": "Return the AGD memory TOC. Pass `file=PATH` to restrict to one file's symbols (much cheaper than the global TOC).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Optional file path (e.g., 'requests/sessions.py'). When set, only blocks belonging to this file are listed."},
            },
        },
    },
    {
        "name": "agd_search",
        "description": "Substring search across block bodies. Returns id + kind + a short excerpt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "ignore_case": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
    },
    {
        "name": "agd_get",
        "description": "Fetch one or more blocks by id. Optionally include backlinks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "Block ids without the leading '#'."},
                "with_backlinks": {"type": "boolean", "default": False,
                                   "description": "If true, the response also includes every block that refs= the given ids."},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "agd_backlinks",
        "description": "List ids that reference (refs=) the given block.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "read_window",
        "description": "Read a contiguous slice of a source file at the repo's checked-out commit. Lines are 1-indexed and inclusive. Use for the few neighbour lines needed to anchor a unified-diff hunk header.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer", "default": 200},
            },
            "required": ["path"],
        },
    },
    {
        "name": "submit_patch",
        "description": "Submit the final unified diff. Call this exactly once when the fix is ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "diff": {"type": "string", "description": "The complete unified diff."},
            },
            "required": ["diff"],
        },
    },
]


def _agd(corpus: Path, args: list[str], timeout: float = 20.0) -> str:
    # agd CLI expects FILE as the first positional after the verb
    if not args:
        return "[agd: empty args]"
    cmd = ["agd", args[0], str(corpus)] + args[1:]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "[agd timeout]"
    if r.returncode != 0:
        return f"[agd error: {r.stderr.strip() or 'rc='+str(r.returncode)}]"
    return r.stdout


# --- BM25 symbol ranking (v3) -------------------------------------------------
# The v0+ failure analysis showed that agd-graph times out without submitting
# whenever the problem statement does NOT mention a file path. Substring search
# (agd_search) returns too many irrelevant matches; the model can't construct
# a file scope. agd_rank_symbols closes that gap by ranking symbols by BM25
# over their (desc + body) against the query. Top result's file= drives the
# subsequent agd_toc(file=...) scope.

import math, re
from collections import Counter

_BM25_CACHE: dict[str, tuple[list[dict], list[list[str]], list[int], float, Counter]] = {}
_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _terms(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOK.finditer(text)]


def _parse_corpus(corpus_path: Path) -> list[dict]:
    """Parse @x-symbol blocks from the AGD corpus.
    Returns list of {id, file, desc, qual, body}. Cheap (~50ms on 600KB).
    """
    text = corpus_path.read_text(errors="replace")
    out: list[dict] = []
    cur: dict | None = None
    in_fence = False
    body_lines: list[str] = []
    block_re = re.compile(
        r'^@x-symbol\s+'
        r'desc="(?P<desc>[^"]*)"\s+'
        r'file="(?P<file>[^"]*)"\s+'
        r'qual=(?P<qual>\S+)'
        r'.*?\[#(?P<id>[^\]]+)\]\s*$'
    )
    for line in text.splitlines():
        if cur is None:
            m = block_re.match(line)
            if m:
                cur = {"id": m.group("id"), "file": m.group("file"),
                       "desc": m.group("desc"), "qual": m.group("qual"),
                       "body": ""}
            continue
        if line.strip() == "~~~":
            if not in_fence:
                in_fence = True
                body_lines = []
            else:
                cur["body"] = "\n".join(body_lines)
                out.append(cur)
                cur = None
                in_fence = False
        elif in_fence:
            body_lines.append(line)
    return out


def _bm25_index(corpus_path: Path):
    """Build (or fetch cached) BM25 index over symbol bodies+descs."""
    key = str(corpus_path)
    if key in _BM25_CACHE:
        return _BM25_CACHE[key]
    syms = _parse_corpus(corpus_path)
    docs_terms = []
    for s in syms:
        # Index desc + first 80 lines of body (caps cost on large symbols)
        body_head = "\n".join(s["body"].splitlines()[:80])
        docs_terms.append(_terms(s["desc"] + " " + s["qual"] + " " + body_head))
    doc_lens = [len(t) for t in docs_terms]
    avgdl = (sum(doc_lens) / len(doc_lens)) if doc_lens else 1.0
    df: Counter[str] = Counter()
    for t in docs_terms:
        df.update(set(t))
    _BM25_CACHE[key] = (syms, docs_terms, doc_lens, avgdl, df)
    return _BM25_CACHE[key]


def _bm25_score(N: int, df_t: int, tf: int, dl: int, avgdl: float,
                k1: float = 1.5, b: float = 0.75) -> float:
    if tf == 0:
        return 0.0
    idf = math.log(1 + (N - df_t + 0.5) / (df_t + 0.5))
    denom = tf + k1 * (1 - b + b * dl / (avgdl or 1))
    return idf * (tf * (k1 + 1)) / (denom or 1)


def _t_rank_symbols(corpus_path: Path, query: str, top_k: int = 12) -> str:
    syms, docs_terms, doc_lens, avgdl, df = _bm25_index(corpus_path)
    if not syms:
        return "[empty corpus]"
    q_terms = list(set(_terms(query)))
    if not q_terms:
        return "[empty query — provide keywords]"
    N = len(syms)
    scored: list[tuple[float, dict]] = []
    for i, sym in enumerate(syms):
        tf_doc = Counter(docs_terms[i])
        score = sum(_bm25_score(N, df.get(t, 0), tf_doc.get(t, 0), doc_lens[i], avgdl) for t in q_terms)
        if score > 0:
            scored.append((score, sym))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return f"[no symbols matched terms: {q_terms}]"
    lines = [f"# top {min(top_k, len(scored))} of {len(scored)} matching symbols (BM25)"]
    for score, sym in scored[:top_k]:
        lines.append(f"{score:6.2f}  {sym['id']}\tfile={sym['file']}\tdesc={sym['desc']}")
    return "\n".join(lines)
# -----------------------------------------------------------------------------


def _t_toc(corpus: Path, file: str | None = None) -> str:
    full = _agd(corpus, ["ids"])
    if not file:
        return full
    # Filter to lines belonging to the requested file.
    file_slug_lower = (
        file.replace("/", "-").replace(".", "-").lower()
    )
    # Trim odd characters: the ingest slug() also keeps underscores and
    # squashes other punctuation; this is a good-enough heuristic for
    # SWE-bench-style paths.
    keep_prefix_heading = f"file--{file_slug_lower}"
    keep_prefix_block = f"{file_slug_lower}--"
    out = []
    for line in full.splitlines():
        first = line.split("\t", 1)[0].strip()
        if first == keep_prefix_heading or first.startswith(keep_prefix_block):
            out.append(line)
    if not out:
        return f"[no symbols found for file '{file}'. Check the path; the slug we tried was '{file_slug_lower}'.]"
    return "\n".join(out)


def _t_read_window(repo: Path, path: str, start: int, end: int) -> str:
    if not repo:
        return "[read_window unavailable: no repo bound]"
    p = (repo / path.lstrip("/")).resolve()
    if not str(p).startswith(str(repo.resolve())):
        return f"[read_window: path escapes repo: {path}]"
    if not p.is_file():
        return f"[read_window: not a file: {path}]"
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError as e:
        return f"[read_window error: {e}]"
    s = max(1, start)
    e = min(len(lines), end)
    if s > len(lines):
        return f"[read_window: file has only {len(lines)} lines]"
    return "\n".join(f"{i:6d}\t{lines[i-1]}" for i in range(s, e + 1))


def _t_search(corpus: Path, query: str, ignore_case: bool = True) -> str:
    args = ["search", "-i"] if ignore_case else ["search"]
    args.append(query)
    return _agd(corpus, args)


def _t_get(corpus: Path, ids: list[str], with_backlinks: bool = False) -> str:
    fids = [f"#{i}" if not i.startswith("#") else i for i in ids]
    args = ["get"] + fids
    if with_backlinks:
        args.append("--with-backlinks")
    return _agd(corpus, args)


def _t_backlinks(corpus: Path, bid: str) -> str:
    fid = bid if bid.startswith("#") else f"#{bid}"
    return _agd(corpus, ["backlinks", fid])


def _cap(text: str) -> str:
    if len(text) <= TOOL_OUTPUT_CAP:
        return text
    return text[:TOOL_OUTPUT_CAP] + f"\n[…truncated, {len(text)-TOOL_OUTPUT_CAP} chars dropped]"


def _short_args(args: dict[str, Any]) -> str:
    """Compact one-line summary of tool args for trajectory logging."""
    parts = []
    for k, v in args.items():
        s = repr(v)
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _dispatch(corpus: Path, repo: Path | None, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "agd_rank_symbols":
            return _cap(_t_rank_symbols(corpus, args["query"], args.get("top_k", 12)))
        if name == "agd_toc":
            return _cap(_t_toc(corpus, args.get("file")))
        if name == "agd_search":
            return _cap(_t_search(corpus, args["query"], args.get("ignore_case", True)))
        if name == "agd_get":
            return _cap(_t_get(corpus, args["ids"], args.get("with_backlinks", False)))
        if name == "agd_backlinks":
            return _cap(_t_backlinks(corpus, args["id"]))
        if name == "read_window":
            return _cap(_t_read_window(repo, args["path"],
                                       args.get("start_line", 1), args.get("end_line", 200)))
    except Exception as e:
        return f"[tool error: {type(e).__name__}: {e}]"
    return f"[unknown tool: {name}]"


class AgdGraph(RetrievalAdapter):
    name = "agd-graph"

    def __init__(self, corpus_path: Path):
        self.corpus = corpus_path
        if not self.corpus.exists():
            raise FileNotFoundError(f"AGD corpus missing: {self.corpus}")

    def run(
        self,
        repo_root: Path,
        problem_statement: str,
        client,
        model: str,
        token_budget: int,
    ) -> AdapterResult:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [
                {"type": "text", "text": f"# Problem\n{problem_statement}\n\n{PATCH_INSTRUCTIONS}"}
            ]}
        ]
        loaded_tokens = len(ENC.encode(SYSTEM)) + len(ENC.encode(problem_statement)) + len(ENC.encode(PATCH_INSTRUCTIONS))
        files_loaded: dict[str, str] = {}
        tool_calls = 0
        api_in = api_out = api_cw = api_cr = 0
        trajectory: list[str] = []

        for turn in range(MAX_TURNS):
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            )
            api_in += resp.usage.input_tokens
            api_out += resp.usage.output_tokens
            api_cr += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            api_cw += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0

            assistant_blocks = list(resp.content)
            messages.append({"role": "assistant", "content": [b.model_dump() for b in assistant_blocks]})

            tool_uses = [b for b in assistant_blocks if b.type == "tool_use"]
            text_blocks = [b for b in assistant_blocks if b.type == "text"]
            text_so_far = "\n".join(b.text for b in text_blocks)

            for tu in tool_uses:
                if tu.name == "submit_patch":
                    diff = (tu.input or {}).get("diff", "")
                    patch = extract_patch(diff) or (diff.strip() + "\n" if diff.strip() else "")
                    return AdapterResult(
                        patch=patch,
                        files_loaded=files_loaded,
                        loaded_tokens=loaded_tokens,
                        tool_calls=tool_calls + 1,
                        api_input_tokens=api_in,
                        api_output_tokens=api_out,
                        api_cache_read=api_cr,
                        api_cache_write=api_cw,
                        notes={"turns": turn + 1, "stop_reason": "submit_patch", "trajectory": trajectory + [f"submit_patch(diff=…{len(diff)}c)"]},
                    )

            if not tool_uses:
                patch = extract_patch(text_so_far)
                return AdapterResult(
                    patch=patch,
                    files_loaded=files_loaded,
                    loaded_tokens=loaded_tokens,
                    tool_calls=tool_calls,
                    api_input_tokens=api_in,
                    api_output_tokens=api_out,
                    api_cache_read=api_cr,
                    api_cache_write=api_cw,
                    notes={"turns": turn + 1, "stop_reason": "no_tool_use", "trajectory": trajectory},
                )

            tool_results = []
            for tu in tool_uses:
                tool_calls += 1
                args_in = tu.input or {}
                trajectory.append(f"{tu.name}({_short_args(args_in)})")
                out = _dispatch(self.corpus, repo_root, tu.name, args_in)
                loaded_tokens += len(ENC.encode(out))
                # best-effort: track which file each fetched block belongs to,
                # by parsing file="..." from agd output
                if tu.name in {"agd_get"}:
                    for line in out.splitlines():
                        if line.startswith("@x-symbol"):
                            import re
                            m = re.search(r'file="([^"]+)"', line)
                            if m:
                                files_loaded[m.group(1)] = files_loaded.get(m.group(1), "") + "\n[block]"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": out,
                })
            messages.append({"role": "user", "content": tool_results})

        return AdapterResult(
            patch="",
            files_loaded=files_loaded,
            loaded_tokens=loaded_tokens,
            tool_calls=tool_calls,
            api_input_tokens=api_in,
            api_output_tokens=api_out,
            api_cache_read=api_cr,
            api_cache_write=api_cw,
            notes={"turns": MAX_TURNS, "stop_reason": "max_turns", "trajectory": trajectory},
        )
