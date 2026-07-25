#!/usr/bin/env python3
"""UserPromptSubmit hook: surface the project memory blocks most relevant
to the user's prompt.

Pipeline (no args, default mode):
    1. read the hook's JSON payload from stdin (field `user_prompt`)
    2. resolve memory file + `agd` binary
    3. apply guard rails (length, code-paste, missing deps)
    4. `agd parse <mem> --json` once, score every block against the
       prompt, take top-K within a char budget
    5. render the chosen blocks via a single `agd get` batch and write
       them to stdout, exit 0

The hook **must never break the user's prompt**: every error path returns
exit 0 with empty stdout (skip reason on stderr iff AGD_RECALL_DEBUG).

CLI subcommand (manual invocation):
    config show                 print effective configuration
    config enable <lang>        add a built-in stopwords language
    config disable <lang>       remove a built-in stopwords language
    config set <l1,l2,...>      replace the list
    config reset                back to defaults (en only)
    config stopwords-file PATH  set a custom stopwords file
    config stopwords-file       (no arg) clear custom file

Persistent config is written to
    ${CLAUDE_PLUGIN_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/agd-memory}/recall.json
and overridden at runtime by env vars `AGD_RECALL_LANGS` and
`AGD_RECALL_STOPWORDS_FILE`.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, TextIO


DEFAULT_TOP_K = 3
DEFAULT_MIN_WORDS = 3
DEFAULT_MIN_WORD_LEN = 3
DEFAULT_TOKEN_BUDGET = 1200

# Relevance floors. Without them `top_k` injects anything scoring > 0:
# measured on a real 21-block memory, "aggiungi DAF alla categoria camion"
# scored 11.74 / 0.39 / 0.38 and all three were injected — a 30x cliff
# ignored, ~690 tokens of noise on that prompt alone. The ratio prunes the
# tail after a cliff; the absolute floor handles the flat case where the
# best match is itself weak (0.51 / 0.48 / 0.44 => inject nothing).
DEFAULT_MIN_SCORE_RATIO = 0.35
DEFAULT_MIN_SCORE = 0.6

# Cross-lingual cognate matching. Memory gets written in whatever
# language the session ran in; prompts arrive in either. Five characters
# is where a shared prefix stops being coincidence, and a cognate hit is
# worth half an exact one — enough to surface the right block, not
# enough to outrank a literal match.
FUZZY_PREFIX_LEN = 5
DEFAULT_FUZZY_WEIGHT = 0.5

# A block matched *only* by cognates needs more than one of them. A
# single shared prefix is weak evidence and does fire on unrelated
# prompts: "category theory" reaches a block about `categoria` trucks,
# "model rocket" reaches one about a `modello commerciale`. Two
# independent cognates is a pattern; one is a coincidence.
FUZZY_ANCHOR_MIN = 2
DEFAULT_MAX_PROMPT_CHARS = 4000
DEFAULT_CODE_LINE_RATIO = 0.4
SUBPROCESS_TIMEOUT_S = 3
DEFAULT_LANGS = ("en",)


# Unicode-aware: matches any letter sequence (Latin, Cyrillic, Greek,
# CJK, etc.) and any digit sequence. Underscore is excluded.
_TOKEN_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


# Built-in stopwords by language. EN is the default; other languages are
# opt-in via `AGD_RECALL_LANGS` or `config enable <lang>`. Add more
# languages here (or contribute upstream) and they become selectable.
BUILTIN_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({
        "about", "after", "again", "all", "also", "and", "any", "are",
        "because", "been", "before", "being", "but", "can", "could",
        "did", "does", "doing", "done", "down", "each", "few", "for",
        "from", "had", "has", "have", "having", "her", "here", "him",
        "his", "how", "into", "its", "just", "more", "most", "myself",
        "not", "now", "off", "only", "other", "our", "out", "over",
        "own", "same", "she", "should", "some", "such", "than", "that",
        "the", "their", "them", "then", "there", "these", "they",
        "this", "those", "through", "too", "under", "until", "very",
        "was", "were", "what", "when", "where", "which", "while", "who",
        "whom", "why", "will", "with", "would", "you", "your", "yours",
    }),
    "it": frozenset({
        "anche", "ancora", "avere", "avevo", "avevi", "aveva",
        "avevamo", "avevate", "avevano", "che", "chi", "come", "con",
        "cosa", "cui", "dai", "dal", "dalla", "dalle", "dallo", "degli",
        "dei", "del", "della", "delle", "dello", "essere", "fra", "gli",
        "hai", "hanno", "ho", "loro", "lui", "lei", "molto", "negli",
        "nei", "nel", "nella", "nelle", "nello", "non", "noi", "per",
        "perche", "perché", "perchè", "poi", "questa", "queste",
        "questi", "questo", "quella", "quelle", "quelli", "quello",
        "qui", "quindi", "sei", "siete", "sono", "stessa", "stesse",
        "stessi", "stesso", "sui", "sul", "sulla", "sulle", "sullo",
        "tra", "tua", "tuo", "tuoi", "tue", "una", "uno", "vai", "voi",
        "vostra", "vostre", "vostri", "vostro",
        # Greetings, temporal adverbs and high-frequency verb forms. These
        # were missing, which is how "ciao come stai oggi" retrieved two
        # project blocks: `oggi` collided with the `.oggi` suffix in a
        # collection name. Deliberately NOT included: `altro`, which is a
        # real category value in at least one corpus.
        "adesso", "allora", "bene", "ciao", "cosi", "così", "dopo",
        "dove", "domani", "faccio", "fai", "fanno", "fare", "fatta",
        "fatte", "fatti", "fatto", "già", "gia", "grazie", "ieri",
        "mai", "male", "meno", "mentre", "ogni", "oggi", "oppure",
        "però", "pero", "piu", "più", "poco", "possiamo", "posso",
        "prego", "prima", "puo", "può", "puoi", "qual", "quale",
        "quali", "quando", "quanta", "quante", "quanti", "quanto",
        "salve", "sempre", "senza", "sopra", "sotto", "sta", "stai",
        "stanno", "stiamo", "sto", "subito", "troppo", "tutta",
        "tutte", "tutti", "tutto", "vediamo", "vedere", "visto",
        "vogliamo", "voglio", "vuoi", "vuole",
    }),
}


@dataclass
class Block:
    id: str
    kind: str
    desc: str | None
    body: str
    refs: list[str] = field(default_factory=list)
    status: str | None = None
    updated: str | None = None
    origin: Path | None = None  # which layer's file this came from


def debug(msg: str, env: Mapping[str, str] | None = None) -> None:
    env = env if env is not None else os.environ
    if env.get("AGD_RECALL_DEBUG"):
        print(f"[agd-memory:recall] {msg}", file=sys.stderr)


def emit(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def read_hook_input(stream: TextIO) -> dict:
    raw = stream.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _config_dir(env: Mapping[str, str]) -> Path:
    base = env.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "agd-memory"
    return Path.home() / ".local" / "share" / "agd-memory"


def _config_path(env: Mapping[str, str]) -> Path:
    return _config_dir(env) / "recall.json"


def _load_config_file(env: Mapping[str, str]) -> dict:
    path = _config_path(env)
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config_file(env: Mapping[str, str], data: dict) -> Path:
    path = _config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _parse_langs(raw: str) -> list[str]:
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def resolve_langs(env: Mapping[str, str]) -> list[str]:
    raw = env.get("AGD_RECALL_LANGS")
    if raw is not None:
        return _parse_langs(raw)
    cfg = _load_config_file(env)
    raw = cfg.get("langs")
    if isinstance(raw, list):
        return [str(x).lower() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return _parse_langs(raw)
    return list(DEFAULT_LANGS)


def _load_stopwords_file(path: Path) -> frozenset[str]:
    try:
        with path.open(encoding="utf-8") as fh:
            words = {line.strip().lower() for line in fh if line.strip()}
        return frozenset(w for w in words if not w.startswith("#"))
    except OSError:
        return frozenset()


def resolve_stopwords(env: Mapping[str, str]) -> frozenset[str]:
    langs = resolve_langs(env)
    bag: set[str] = set()
    for lg in langs:
        bag |= BUILTIN_STOPWORDS.get(lg, frozenset())

    extra_path: Path | None = None
    raw_env = env.get("AGD_RECALL_STOPWORDS_FILE")
    if raw_env:
        extra_path = Path(raw_env).expanduser()
    else:
        cfg = _load_config_file(env)
        cfg_extra = cfg.get("stopwords_file")
        if isinstance(cfg_extra, str) and cfg_extra:
            extra_path = Path(cfg_extra).expanduser()

    if extra_path is not None and extra_path.is_file():
        bag |= _load_stopwords_file(extra_path)

    return frozenset(bag)


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
try:
    from agd_memory_paths import global_memory_file, project_memory_file
except ImportError:  # pragma: no cover — hook must degrade, never break
    global_memory_file = project_memory_file = None


def resolve_memory_file(env: Mapping[str, str]) -> Path | None:
    if project_memory_file is None:
        override = env.get("AGD_MEMORY_FILE")
        if override:
            p = Path(override).expanduser()
            return p if p.is_file() else None
        cwd = Path(env.get("AGD_MEMORY_PROJECT_CWD") or os.getcwd()).resolve()
        sanitized = str(cwd).replace("/", "-")
        p = Path.home() / ".claude" / "projects" / sanitized / "memory" / "memory.agd"
        return p if p.is_file() else None
    p = project_memory_file(env)
    return p if p.is_file() else None


def resolve_memory_files(env: Mapping[str, str]) -> list[Path]:
    """Every layer auto-recall should score against, project first.

    A standing preference ("never push without review") is exactly the
    kind of thing that must surface on any prompt in any repo, so the
    global layer participates in ranking rather than sitting in a file
    nobody reads.
    """
    files = []
    project = resolve_memory_file(env)
    if project is not None:
        files.append(project)
    if not env.get("AGD_RECALL_NO_GLOBAL") and global_memory_file is not None:
        g = global_memory_file(env)
        if g.is_file() and g not in files:
            files.append(g)
    return files


def resolve_agd_bin(env: Mapping[str, str]) -> Path | None:
    candidate = Path(env.get("AGD_BIN", str(Path.home() / ".cargo/bin/agd")))
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def tokenize(text: str, *, min_len: int = DEFAULT_MIN_WORD_LEN) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= min_len]


def strip_stopwords(
    tokens: Iterable[str], stop: frozenset[str]
) -> list[str]:
    return [t for t in tokens if t not in stop]


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_refs(attrs: dict) -> list[str]:
    raw = attrs.get("refs")
    if not isinstance(raw, str):
        return []
    out = []
    for r in raw.split(","):
        r = r.strip().lstrip("#")
        if r:
            out.append(r)
    return out


def _body_from_content(content: dict) -> str:
    if not isinstance(content, dict):
        return ""
    ctype = content.get("type")
    if ctype == "fenced":
        return content.get("value") or ""
    if ctype == "inline":
        parts = content.get("value") or []
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )
    if ctype == "items":
        rows = content.get("value") or []
        lines = []
        for row in rows:
            text = "".join(
                p.get("text", "") for p in row if isinstance(p, dict)
            )
            lines.append(f"- {text}")
        return "\n".join(lines)
    return ""


def load_blocks(agd_bin: Path, mem: Path) -> list[Block]:
    try:
        out = subprocess.run(
            [str(agd_bin), "parse", str(mem), "--json"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if out.returncode != 0:
        return []
    try:
        doc = json.loads(out.stdout)
    except json.JSONDecodeError:
        return []
    raw_blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(raw_blocks, list):
        return []
    blocks: list[Block] = []
    for b in raw_blocks:
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        if not bid:
            continue
        attrs = b.get("attrs") or {}
        blocks.append(Block(
            id=bid,
            kind=b.get("kind", "") or "",
            desc=attrs.get("desc"),
            body=_body_from_content(b.get("content") or {}),
            refs=_parse_refs(attrs),
            status=attrs.get("status"),
            updated=attrs.get("updated"),
            origin=mem,
        ))
    return blocks


def load_all_blocks(agd_bin: Path, mems: Iterable[Path]) -> list[Block]:
    """Blocks from every layer, scored as one corpus.

    Ids are namespaced per file, so the same id in two layers is kept
    separately — the project's version simply outranks the global one on
    the recency tiebreak when both match.
    """
    blocks: list[Block] = []
    seen: set[tuple[str, str]] = set()
    for mem in mems:
        for b in load_blocks(agd_bin, mem):
            key = (str(mem), b.id)
            if key not in seen:
                seen.add(key)
                blocks.append(b)
    return blocks


def _recency_key(block: Block) -> int:
    """Sort key placing recently-updated blocks first (0 when undated)."""
    if not block.updated:
        return 0
    digits = re.sub(r"\D", "", block.updated)[:8]
    return -int(digits) if len(digits) == 8 else 0


def _anchor_token_set(block: Block) -> set[str]:
    """Tokens from the *curated* fields: id and desc.

    These are written deliberately to say what the block is about, and
    `desc` is populated on 96% of the corpus. A prompt token found here
    is about the block's subject; one found only in the fenced body may
    be an incidental mention — "lista" inside a prose paragraph is not
    evidence the block answers a question about sorting a list.
    """
    return set(tokenize(block.id.replace("-", " "))) | set(tokenize(block.desc or ""))


def _block_token_set(block: Block) -> set[str]:
    """Every distinct token a block contributes to the corpus vocabulary."""
    toks = _anchor_token_set(block)
    toks |= set(tokenize((block.kind or "").replace("x-", " ")))
    toks |= set(tokenize(block.body))
    return toks


def compute_idf(blocks: list[Block]) -> dict[str, float]:
    """Smoothed inverse document frequency over the block corpus. A token in
    every block carries no signal; a token in a single block is distinctive.
    idf(t) = log((N+1)/(df(t)+1)) + 1 — always >= 1, so a match never scores
    negative and a rare term outweighs a common one. Measured from the file's
    own vocabulary, not a fixed table."""
    n = len(blocks)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for b in blocks:
        for t in _block_token_set(b):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def _prefix_index(tokens: Iterable[str]) -> dict[str, set[str]]:
    """Group tokens by their first `FUZZY_PREFIX_LEN` characters.

    The cross-lingual case this exists for: memory written in Italian,
    prompt typed in English. Exact matching only bridges the two through
    loanwords (`server`, `browser`, `deploy`). But the technical
    vocabulary the two languages share is largely Latinate cognates that
    agree on a long prefix and diverge at the suffix —
    catalog/catalogo, model/modello, pagination/paginazione,
    production/produzione. Bucketing by prefix finds those in O(1) per
    token, with no dictionary to curate and no model to load.

    Short tokens are excluded: five characters is where a shared prefix
    stops being coincidence.
    """
    idx: dict[str, set[str]] = {}
    for t in tokens:
        if len(t) >= FUZZY_PREFIX_LEN:
            idx.setdefault(t[:FUZZY_PREFIX_LEN], set()).add(t)
    return idx


def _field_hits(
    prompt_tokens: set[str],
    field_tokens: Iterable[str],
    prefix_idx: dict[str, set[str]] | None,
) -> tuple[set[str], set[str]]:
    """(exact, fuzzy) prompt tokens this field matches.

    A prompt token already matched exactly never also counts as fuzzy —
    otherwise a word would be paid for twice.
    """
    field_set = field_tokens if isinstance(field_tokens, set) else set(field_tokens)
    exact = prompt_tokens & field_set
    if not prefix_idx:
        return exact, set()
    fuzzy: set[str] = set()
    for tok in field_set:
        if len(tok) < FUZZY_PREFIX_LEN:
            continue
        for cand in prefix_idx.get(tok[:FUZZY_PREFIX_LEN], ()):
            if cand not in exact:
                fuzzy.add(cand)
    return exact, fuzzy


def score_block(
    prompt_tokens: set[str],
    block: Block,
    idf: dict[str, float] | None = None,
    prefix_idx: dict[str, set[str]] | None = None,
    fuzzy_weight: float = DEFAULT_FUZZY_WEIGHT,
) -> float:
    if not prompt_tokens:
        return 0.0
    id_tokens = set(tokenize(block.id.replace("-", " ")))
    desc_tokens = set(tokenize(block.desc or ""))
    kind_tokens = set(tokenize((block.kind or "").replace("x-", " ")))
    body_tokens = tokenize(block.body)

    # idf=None keeps the original uniform weighting (each hit counts 1).
    def wt(t: str) -> float:
        return 1.0 if idf is None else idf.get(t, 1.0)

    def field(tokens, boost: float) -> float:
        exact, fuzzy = _field_hits(prompt_tokens, tokens, prefix_idx)
        return boost * (
            sum(wt(t) for t in exact) + fuzzy_weight * sum(wt(t) for t in fuzzy)
        )

    raw = field(id_tokens, 3) + field(desc_tokens, 3) + field(kind_tokens, 2)

    # The body scores per occurrence, not per distinct token, so repeated
    # mentions still count for more than a single passing one.
    _, body_fuzzy = _field_hits(prompt_tokens, set(body_tokens), prefix_idx)
    for t in body_tokens:
        if t in prompt_tokens:
            raw += wt(t)
    if body_fuzzy:
        fuzzy_srcs = {
            tok for tok in set(body_tokens)
            if len(tok) >= FUZZY_PREFIX_LEN
            and prefix_idx
            and prefix_idx.get(tok[:FUZZY_PREFIX_LEN])
        }
        for t in body_tokens:
            if t in fuzzy_srcs and t not in prompt_tokens:
                raw += fuzzy_weight * wt(t)

    if raw <= 0:
        return 0.0
    return raw / (1.0 + math.log1p(len(body_tokens)))


def top_k_blocks(
    blocks: list[Block],
    prompt_tokens: set[str],
    k: int,
    *,
    min_score: float = 0.0,
    min_score_ratio: float = 0.0,
    require_anchor: bool = False,
    skip_superseded: bool = False,
    fuzzy: bool = False,
) -> list[Block]:
    """Rank blocks and keep at most `k`, subject to four relevance filters.

    `min_score_ratio` is relative to the best block: a hit scoring less
    than `ratio * top` is tail noise sitting below a cliff. `min_score`
    is absolute and catches the case where nothing matched well — there
    the honest answer is to inject nothing rather than the least-bad
    three. `require_anchor` drops blocks whose only overlap with the
    prompt is inside the fenced body (see `_anchor_token_set`).
    `skip_superseded` drops blocks a later entry has explicitly
    replaced. `fuzzy` additionally credits cross-lingual cognates (see
    `_prefix_index`). All default to off, preserving the raw ranking for
    callers that want it.

    Ties break on recency first: two equally relevant blocks are not
    equally good answers if one of them is a year older.
    """
    idf = compute_idf(blocks)
    prefix_idx = _prefix_index(prompt_tokens) if fuzzy else None
    scored = []
    for b in blocks:
        if skip_superseded and b.status == "superseded":
            continue
        s = score_block(prompt_tokens, b, idf, prefix_idx)
        if s <= 0:
            continue
        if require_anchor:
            exact, fuzzy_hits = _field_hits(
                prompt_tokens, _anchor_token_set(b), prefix_idx
            )
            if not exact and len(fuzzy_hits) < FUZZY_ANCHOR_MIN:
                continue
        scored.append((s, b))
    if not scored:
        return []
    scored.sort(
        key=lambda sb: (-sb[0], _recency_key(sb[1]), len(sb[1].body), sb[1].id)
    )
    floor = max(min_score, scored[0][0] * min_score_ratio)
    return [b for s, b in scored[:k] if s >= floor]


def _render_one_block(b: Block) -> str:
    parts = []
    if b.desc:
        parts.append(f'desc="{b.desc}"')
    if b.refs:
        parts.append('refs="' + ",".join(f"#{r}" for r in b.refs) + '"')
    head = f"@{b.kind}"
    if parts:
        head += " " + " ".join(parts)
    head += f" [#{b.id}]"
    return f"{head}\n~~~\n{b.body}\n~~~\n"


def _agd_get_batch(agd_bin: Path, mem: Path, ids: list[str]) -> str | None:
    if not ids:
        return None
    args = [str(agd_bin), "get", str(mem), *[f"#{i}" for i in ids]]
    try:
        out = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def fits_budget(blocks: list[Block], budget_chars: int) -> list[Block]:
    kept: list[Block] = []
    total = 0
    for b in blocks:
        rendered_len = len(_render_one_block(b))
        if kept and total + rendered_len > budget_chars:
            break
        kept.append(b)
        total += rendered_len
        if total >= budget_chars:
            break
    return kept


def _looks_like_code(prompt: str, threshold: float) -> bool:
    if "```" in prompt:
        return True
    lines = [ln for ln in prompt.splitlines() if ln.strip()]
    if len(lines) < 5:
        return False
    indented = sum(1 for ln in lines if ln[:1] in (" ", "\t"))
    return (indented / len(lines)) >= threshold


def should_skip(
    prompt: str,
    env: Mapping[str, str],
    *,
    stopwords: frozenset[str] | None = None,
) -> str | None:
    if env.get("AGD_RECALL_DISABLED"):
        return "disabled_via_env"
    if not prompt or not prompt.strip():
        return "prompt_empty"
    max_chars = _env_int(env, "AGD_RECALL_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS)
    if len(prompt) > max_chars:
        return "prompt_too_long"
    ratio = _env_float(env, "AGD_RECALL_CODE_LINE_RATIO", DEFAULT_CODE_LINE_RATIO)
    if _looks_like_code(prompt, ratio):
        return "code_paste_detected"
    min_word_len = _env_int(env, "AGD_RECALL_MIN_WORD_LEN", DEFAULT_MIN_WORD_LEN)
    min_words = _env_int(env, "AGD_RECALL_MIN_WORDS", DEFAULT_MIN_WORDS)
    stop = stopwords if stopwords is not None else resolve_stopwords(env)
    tokens = strip_stopwords(tokenize(prompt, min_len=min_word_len), stop)
    if len(tokens) < min_words:
        return "prompt_below_min_words"
    return None


def _safe_main(stdin: TextIO, env: Mapping[str, str]) -> int:
    payload = read_hook_input(stdin)
    prompt = payload.get("user_prompt") or ""

    stopwords = resolve_stopwords(env)

    skip_reason = should_skip(prompt, env, stopwords=stopwords)
    if skip_reason:
        debug(f"skip: {skip_reason}", env)
        return 0

    agd_bin = resolve_agd_bin(env)
    if agd_bin is None:
        debug("skip: agd_binary_missing", env)
        return 0
    mems = resolve_memory_files(env)
    if not mems:
        debug("skip: memory_file_missing", env)
        return 0

    min_word_len = _env_int(env, "AGD_RECALL_MIN_WORD_LEN", DEFAULT_MIN_WORD_LEN)
    top_k = _env_int(env, "AGD_RECALL_TOP_K", DEFAULT_TOP_K)
    token_budget = _env_int(env, "AGD_RECALL_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET)
    budget_chars = token_budget * 4

    prompt_tokens = set(strip_stopwords(
        tokenize(prompt, min_len=min_word_len), stopwords,
    ))
    blocks = load_all_blocks(agd_bin, mems)
    if not blocks:
        debug("skip: no_blocks_loaded", env)
        return 0

    chosen = top_k_blocks(
        blocks,
        prompt_tokens,
        top_k,
        min_score=_env_float(env, "AGD_RECALL_MIN_SCORE", DEFAULT_MIN_SCORE),
        min_score_ratio=_env_float(
            env, "AGD_RECALL_MIN_SCORE_RATIO", DEFAULT_MIN_SCORE_RATIO
        ),
        require_anchor=not env.get("AGD_RECALL_ALLOW_BODY_ONLY"),
        skip_superseded=not env.get("AGD_RECALL_ALLOW_SUPERSEDED"),
        fuzzy=not env.get("AGD_RECALL_NO_FUZZY"),
    )
    if not chosen:
        debug("skip: no_relevant_blocks", env)
        return 0

    kept = fits_budget(chosen, budget_chars)
    if not kept:
        debug("skip: budget_exhausted", env)
        return 0

    # One batch per source file: `agd get` addresses a single document.
    chunks = []
    for mem in mems:
        ids = [b.id for b in kept if b.origin == mem]
        if not ids:
            continue
        rendered = _agd_get_batch(agd_bin, mem, ids)
        if rendered is None:
            rendered = "".join(
                _render_one_block(b) for b in kept if b.origin == mem
            )
        chunks.append(rendered)
    rendered = "".join(chunks)

    debug(f"injected {len(kept)} block(s): {[b.id for b in kept]}", env)
    header = f"[agd-memory:auto-recall] {len(kept)} block(s) selected for this prompt.\n\n"
    emit(header + rendered)
    return 0


# -- config CLI --------------------------------------------------------------

def _cmd_show(env: Mapping[str, str], _args: list[str]) -> int:
    cfg = _load_config_file(env)
    langs = resolve_langs(env)
    src = "env (AGD_RECALL_LANGS)" if env.get("AGD_RECALL_LANGS") else (
        "config file" if "langs" in cfg else "default"
    )
    print(f"config file: {_config_path(env)}")
    print(f"langs:       {','.join(langs)}   (source: {src})")
    sw_file_env = env.get("AGD_RECALL_STOPWORDS_FILE")
    sw_file_cfg = cfg.get("stopwords_file")
    if sw_file_env:
        print(f"stopwords_file: {sw_file_env}   (source: env)")
    elif sw_file_cfg:
        print(f"stopwords_file: {sw_file_cfg}   (source: config file)")
    else:
        print("stopwords_file: (none)")
    print(f"available built-in langs: {','.join(sorted(BUILTIN_STOPWORDS))}")
    return 0


def _save_langs(env: Mapping[str, str], langs: list[str]) -> Path:
    cfg = _load_config_file(env)
    cfg["langs"] = langs
    return _write_config_file(env, cfg)


def _cmd_enable(env: Mapping[str, str], args: list[str]) -> int:
    if not args:
        print("usage: config enable <lang>", file=sys.stderr)
        return 2
    lang = args[0].lower()
    if lang not in BUILTIN_STOPWORDS:
        avail = ",".join(sorted(BUILTIN_STOPWORDS))
        print(f"unknown built-in lang: {lang!r}. available: {avail}", file=sys.stderr)
        return 2
    current = resolve_langs(env)
    if lang in current:
        print(f"already enabled: {lang}. langs = {','.join(current)}")
        return 0
    current.append(lang)
    path = _save_langs(env, current)
    print(f"enabled {lang}. langs = {','.join(current)}")
    print(f"saved to {path}")
    return 0


def _cmd_disable(env: Mapping[str, str], args: list[str]) -> int:
    if not args:
        print("usage: config disable <lang>", file=sys.stderr)
        return 2
    lang = args[0].lower()
    current = resolve_langs(env)
    if lang not in current:
        print(f"not enabled: {lang}. langs = {','.join(current)}")
        return 0
    current = [x for x in current if x != lang]
    path = _save_langs(env, current)
    print(f"disabled {lang}. langs = {','.join(current) or '(empty)'}")
    print(f"saved to {path}")
    return 0


def _cmd_set(env: Mapping[str, str], args: list[str]) -> int:
    if not args:
        print("usage: config set <lang1,lang2,...>", file=sys.stderr)
        return 2
    langs = _parse_langs(args[0])
    unknown = [lg for lg in langs if lg not in BUILTIN_STOPWORDS]
    if unknown:
        avail = ",".join(sorted(BUILTIN_STOPWORDS))
        print(
            f"unknown built-in lang(s): {','.join(unknown)}. available: {avail}",
            file=sys.stderr,
        )
        return 2
    path = _save_langs(env, langs)
    print(f"set langs = {','.join(langs) or '(empty)'}")
    print(f"saved to {path}")
    return 0


def _cmd_reset(env: Mapping[str, str], _args: list[str]) -> int:
    path = _config_path(env)
    if path.is_file():
        path.unlink()
        print(f"removed {path}")
    else:
        print("no config file to remove")
    print(f"langs = {','.join(DEFAULT_LANGS)} (default)")
    return 0


def _cmd_stopwords_file(env: Mapping[str, str], args: list[str]) -> int:
    cfg = _load_config_file(env)
    if not args:
        cfg.pop("stopwords_file", None)
        path = _write_config_file(env, cfg)
        print(f"cleared stopwords_file. saved to {path}")
        return 0
    p = Path(args[0]).expanduser()
    if not p.is_file():
        print(f"file does not exist: {p}", file=sys.stderr)
        return 2
    cfg["stopwords_file"] = str(p)
    path = _write_config_file(env, cfg)
    print(f"set stopwords_file = {p}")
    print(f"saved to {path}")
    return 0


_CONFIG_HANDLERS = {
    "show": _cmd_show,
    "enable": _cmd_enable,
    "disable": _cmd_disable,
    "set": _cmd_set,
    "reset": _cmd_reset,
    "stopwords-file": _cmd_stopwords_file,
}


def _config_usage() -> int:
    print(
        "usage: agd-memory-recall.py config <show|enable|disable|set|reset|stopwords-file> [args]",
        file=sys.stderr,
    )
    return 2


def run_config(argv: list[str], env: Mapping[str, str]) -> int:
    if not argv:
        return _config_usage()
    sub = argv[0]
    handler = _CONFIG_HANDLERS.get(sub)
    if handler is None:
        print(f"unknown config subcommand: {sub!r}", file=sys.stderr)
        return _config_usage()
    return handler(env, argv[1:])


def main(
    argv: list[str] | None = None,
    stdin: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    argv = list(sys.argv if argv is None else argv)
    env = env if env is not None else os.environ
    if len(argv) > 1 and argv[1] == "config":
        return run_config(argv[2:], env)
    stdin = stdin if stdin is not None else sys.stdin
    try:
        return _safe_main(stdin, env)
    except Exception as e:  # noqa: BLE001 — must swallow to never break prompt
        debug(f"unhandled: {e!r}", env)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
