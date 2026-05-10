#!/usr/bin/env python3
"""Preload-BM25 adapter.

Realistic baseline for "ship as much repo as fits". Pure file-level
BM25 ranks .py files in the repo by relevance to the problem
statement, fills a token budget, and ships everything in one shot.

This is what an agent does when it has no addressable memory and no
tool-use: pick the most relevant files and pray.

Why BM25 over random/all: shipping a random subset is a strawman; an
honest baseline retrieves *something*. BM25 is the standard
file-level IR baseline — well understood, no embeddings, fast.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import tiktoken

from .base import RetrievalAdapter, AdapterResult, PATCH_INSTRUCTIONS, extract_patch

ENC = tiktoken.get_encoding("cl100k_base")
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _walk_py(repo: Path) -> list[Path]:
    out = []
    for p in repo.rglob("*.py"):
        if any(seg in {".git", "__pycache__", ".tox", "node_modules", "build", "dist"} for seg in p.parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def _terms(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN.finditer(text)]


def _bm25_ranking(
    query_terms: list[str],
    docs: list[tuple[Path, str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[Path, float]]:
    N = len(docs)
    if N == 0:
        return []
    doc_terms = [_terms(text) for _, text in docs]
    doc_lens = [len(t) for t in doc_terms]
    avgdl = sum(doc_lens) / N if N else 1
    df: Counter[str] = Counter()
    for t in doc_terms:
        for term in set(t):
            df[term] += 1

    def idf(term: str) -> float:
        n = df.get(term, 0)
        return math.log(1 + (N - n + 0.5) / (n + 0.5))

    scored: list[tuple[Path, float]] = []
    qt = list(set(query_terms))
    for (path, _), tokens, dl in zip(docs, doc_terms, doc_lens):
        tf = Counter(tokens)
        score = 0.0
        for term in qt:
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + k1 * (1 - b + b * dl / (avgdl or 1))
            score += idf(term) * (f * (k1 + 1)) / (denom or 1)
        scored.append((path, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


class PreloadBM25(RetrievalAdapter):
    name = "preload-bm25"

    def run(
        self,
        repo_root: Path,
        problem_statement: str,
        client,
        model: str,
        token_budget: int,
    ) -> AdapterResult:
        py_files = _walk_py(repo_root)
        docs: list[tuple[Path, str]] = []
        for p in py_files:
            try:
                docs.append((p, p.read_text(errors="replace")))
            except OSError:
                continue

        q = _terms(problem_statement)
        ranked = _bm25_ranking(q, docs)

        # Fill budget greedily by rank.
        loaded: dict[str, str] = {}
        used = 0
        prelude_tokens = len(ENC.encode(PATCH_INSTRUCTIONS)) + len(ENC.encode(problem_statement)) + 200
        budget = max(0, token_budget - prelude_tokens)

        for path, score in ranked:
            text = next((t for p, t in docs if p == path), None)
            if text is None:
                continue
            tcount = len(ENC.encode(text))
            if tcount > budget - used:
                # Don't truncate mid-file; skip and try smaller files later.
                if (budget - used) < 200:
                    break
                continue
            rel = str(path.relative_to(repo_root))
            loaded[rel] = text
            used += tcount

        # Compose context
        chunks = [PATCH_INSTRUCTIONS, "\n# Problem statement\n", problem_statement, "\n# Repository excerpt\n"]
        for rel, text in loaded.items():
            chunks.append(f"\n--- file: {rel} ---\n{text}")
        chunks.append("\n# Now produce the unified diff.\n")
        prompt = "".join(chunks)
        loaded_tokens = len(ENC.encode(prompt))

        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text_out = "".join(b.text for b in resp.content if b.type == "text")
        patch = extract_patch(text_out)

        u = resp.usage
        return AdapterResult(
            patch=patch,
            files_loaded=loaded,
            loaded_tokens=loaded_tokens,
            tool_calls=1,
            api_input_tokens=u.input_tokens,
            api_output_tokens=u.output_tokens,
            api_cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            api_cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
            notes={
                "files_considered": len(docs),
                "files_loaded": len(loaded),
                "top_scores": [{"path": str(p.relative_to(repo_root)), "score": round(s, 2)} for p, s in ranked[:10]],
            },
        )
