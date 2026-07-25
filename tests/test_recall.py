"""Tests for the auto-recall hook (`hooks/agd-memory-recall.py`).

Covers tokenisation, stopword removal, scoring, top-K + tie-breaking,
char-budget, skip conditions, output format, and the end-to-end
`main()` entry point reading hook JSON from stdin.
"""
from __future__ import annotations

import io
import json
import os

import pytest


# -- tokenize ----------------------------------------------------------------

def test_tokenize_basic_lowercase_punct_strip(recall):
    assert recall.tokenize("Hello, World!", min_len=1) == ["hello", "world"]


def test_tokenize_keeps_italian_accents(recall):
    out = recall.tokenize("perché città però")
    assert "perché" in out
    assert "città" in out
    assert "però" in out


def test_tokenize_drops_short_tokens(recall):
    out = recall.tokenize("a ab abc abcd", min_len=3)
    assert out == ["abc", "abcd"]


def test_default_stopwords_remove_english_only(recall):
    # Default langs = ("en",) — "che" (italian) survives, "the" filtered.
    # Use dict(os.environ) so the fixture's CLAUDE_PLUGIN_DATA is honoured.
    tokens = ["the", "ricordi", "and", "che", "blog"]
    stop = recall.resolve_stopwords(dict(os.environ))
    out = recall.strip_stopwords(tokens, stop)
    assert "the" not in out
    assert "and" not in out
    assert "che" in out  # italian word survives by default
    assert "ricordi" in out
    assert "blog" in out


def test_italian_stopwords_opt_in_via_AGD_RECALL_LANGS(recall):
    env = dict(os.environ, AGD_RECALL_LANGS="it,en")
    tokens = ["the", "ricordi", "and", "che", "blog"]
    stop = recall.resolve_stopwords(env)
    out = recall.strip_stopwords(tokens, stop)
    assert "the" not in out
    assert "and" not in out
    assert "che" not in out  # filtered now
    assert "ricordi" in out
    assert "blog" in out


def test_unknown_lang_is_silently_ignored(recall):
    env = dict(os.environ, AGD_RECALL_LANGS="klingon,en")
    stop = recall.resolve_stopwords(env)
    assert "the" in stop  # EN list still applied
    # No exception raised — unknown lang yields empty contribution.


def test_resolve_stopwords_loads_extra_file_via_env(recall, tmp_path):
    sw_file = tmp_path / "custom-stops.txt"
    sw_file.write_text("custom1\ncustom2\n# this is a comment\n\n")
    env = dict(os.environ, AGD_RECALL_STOPWORDS_FILE=str(sw_file))
    stop = recall.resolve_stopwords(env)
    assert "custom1" in stop
    assert "custom2" in stop
    assert "the" in stop  # default EN still loaded


def test_tokenize_handles_non_latin_scripts(recall):
    # Cyrillic
    out = recall.tokenize("Привет мир test", min_len=1)
    assert "привет" in out
    assert "мир" in out
    assert "test" in out
    # Greek
    out = recall.tokenize("γεια σου κόσμε", min_len=1)
    assert "γεια" in out
    assert "κόσμε" in out
    # German umlauts + eszett
    out = recall.tokenize("Schöne grüße straße", min_len=1)
    assert "schöne" in out
    assert "grüße" in out
    assert "straße" in out
    # Spanish
    out = recall.tokenize("año mañana niño", min_len=1)
    assert "año" in out
    assert "mañana" in out
    assert "niño" in out


# -- scoring -----------------------------------------------------------------

def _block(recall, **overrides):
    defaults = dict(id="b1", kind="x-user", desc=None, body="", refs=[])
    defaults.update(overrides)
    return recall.Block(**defaults)


def test_score_id_match_weighs_3x(recall):
    b = _block(recall, id="user-blog")
    s = recall.score_block({"blog"}, b)
    # id-only hit: 3 / (1 + log1p(0)) = 3
    assert s == pytest.approx(3.0)


def test_score_desc_match_weighs_3x(recall):
    b = _block(recall, desc="stile prosa del blog")
    s = recall.score_block({"blog"}, b)
    assert s == pytest.approx(3.0)


def test_score_kind_match_weighs_2x(recall):
    b = _block(recall, kind="x-feedback")
    s = recall.score_block({"feedback"}, b)
    assert s == pytest.approx(2.0)


def test_score_body_hit_count_with_log_normalisation(recall):
    import math
    body = " ".join(["blog"] * 5) + " " + " ".join(["xyz"] * 5)
    b = _block(recall, body=body)
    # body has 10 tokens, 5 hits of "blog"
    s = recall.score_block({"blog"}, b)
    expected = 5.0 / (1.0 + math.log1p(10))
    assert s == pytest.approx(expected)


def test_score_zero_when_no_overlap(recall):
    b = _block(recall, id="user-foo", desc="something else", body="nothing here")
    assert recall.score_block({"blog"}, b) == 0.0


# -- IDF weighting (C2) ------------------------------------------------------

def _idf_corpus(recall):
    # 'python' appears in 3/4 blocks (common), 'kafka' in 1/4 (distinctive).
    return [
        _block(recall, id="b1", body="python notes"),
        _block(recall, id="b2", body="python tips"),
        _block(recall, id="b3", body="python again"),
        _block(recall, id="b4", body="kafka setup"),
    ]


def test_compute_idf_rewards_rarer_tokens(recall):
    idf = recall.compute_idf(_idf_corpus(recall))
    assert idf["kafka"] > idf["python"]
    assert idf["python"] >= 1.0  # smoothed: a match is never worth less than 1


def test_idf_ranks_distinctive_match_above_common(recall):
    corpus = _idf_corpus(recall)
    idf = recall.compute_idf(corpus)
    s_rare = recall.score_block({"kafka"}, corpus[3], idf)
    s_common = recall.score_block({"python"}, corpus[0], idf)
    assert s_rare > s_common


def test_score_block_idf_none_preserves_uniform_weighting(recall):
    # Backward compatibility: no idf map → the original per-hit weighting.
    b = _block(recall, id="user-blog")
    assert recall.score_block({"blog"}, b, None) == pytest.approx(3.0)


def test_compute_idf_empty_corpus_is_safe(recall):
    assert recall.compute_idf([]) == {}


# -- top_k_blocks ------------------------------------------------------------

def test_top_k_drops_zero_score_blocks(recall):
    blocks = [
        _block(recall, id="hit", desc="blog rules"),
        _block(recall, id="miss-1"),
        _block(recall, id="miss-2"),
    ]
    out = recall.top_k_blocks(blocks, {"blog"}, k=3)
    assert len(out) == 1
    assert out[0].id == "hit"


def test_top_k_tie_break_prefers_shorter_body(recall):
    short = _block(recall, id="a", desc="blog", body="short")
    long = _block(recall, id="b", desc="blog", body="much longer body content here padding")
    out = recall.top_k_blocks([long, short], {"blog"}, k=1)
    assert out[0].id == "a"


def test_top_k_tie_break_then_id_lex_order(recall):
    a = _block(recall, id="aaa", desc="blog", body="")
    b = _block(recall, id="bbb", desc="blog", body="")
    out = recall.top_k_blocks([b, a], {"blog"}, k=2)
    assert [x.id for x in out] == ["aaa", "bbb"]


def test_top_k_floors_default_off_preserving_raw_ranking(recall):
    """Callers that pass no floors still get the take-everything ranking."""
    blocks = [
        _block(recall, id="strong", desc="blog rules"),
        _block(recall, id="weak", body="an incidental blog mention " + "pad " * 80),
    ]
    assert len(recall.top_k_blocks(blocks, {"blog"}, k=3)) == 2


def test_min_score_ratio_prunes_the_tail_below_a_cliff(recall):
    """A block scoring far under the best one is tail noise, not a hit."""
    blocks = [
        _block(recall, id="camion-categoria", desc="categoria camion DAF"),
        _block(recall, id="other", desc="unrelated", body="camion " + "pad " * 200),
    ]
    out = recall.top_k_blocks(
        blocks, {"categoria", "camion", "daf"}, k=3, min_score_ratio=0.35
    )
    assert [b.id for b in out] == ["camion-categoria"]


def test_min_score_injects_nothing_when_every_match_is_weak(recall):
    """Flat, uniformly weak scores mean nothing is relevant — inject nothing
    rather than the least-bad three."""
    blocks = [
        _block(recall, id=f"b{i}", desc="blog", body="pad " * 300) for i in range(3)
    ]
    assert recall.top_k_blocks(blocks, {"blog"}, k=3, min_score=5.0) == []


def test_require_anchor_drops_body_only_matches(recall):
    """"lista" appearing inside a prose body is not evidence the block is
    about sorting a list; a match in id/desc is."""
    body_only = _block(recall, id="trucks", desc="categoria camion",
                       body="estendere la lista dell'alias trucks")
    anchored = _block(recall, id="lista-ordinamento", desc="come ordinare una lista")
    out = recall.top_k_blocks([body_only, anchored], {"lista"}, k=3, require_anchor=True)
    assert [b.id for b in out] == ["lista-ordinamento"]


def test_require_anchor_keeps_desc_match_even_with_long_body(recall):
    b = _block(recall, id="devtools-perf", desc="Browser lento durante la raccolta",
               body="pad " * 300)
    out = recall.top_k_blocks([b], {"browser"}, k=3, require_anchor=True)
    assert [x.id for x in out] == ["devtools-perf"]


def test_fuzzy_bridges_italian_memory_and_english_prompt(recall):
    """The case the whole mechanism exists for: memory written in one
    language, prompt typed in the other, joined by a Latinate cognate."""
    b = _block(recall, id="catalogo-unico", desc="creato il catalogo unico")
    assert recall.top_k_blocks([b], {"catalogue"}, k=1) == []
    out = recall.top_k_blocks([b], {"catalogue"}, k=1, fuzzy=True)
    assert [x.id for x in out] == ["catalogo-unico"]


def test_fuzzy_ignores_short_tokens(recall):
    """Four characters of shared prefix is coincidence, not a cognate."""
    b = _block(recall, id="modo", desc="modo operativo")
    assert recall.top_k_blocks([b], {"mode"}, k=1, fuzzy=True) == []


def test_fuzzy_hit_scores_below_an_exact_hit(recall):
    exact = _block(recall, id="pagination", desc="pagination logic")
    cognate = _block(recall, id="paginazione", desc="paginazione logica")
    out = recall.top_k_blocks(
        [cognate, exact], {"pagination"}, k=2, fuzzy=True
    )
    assert [b.id for b in out] == ["pagination", "paginazione"]


def test_fuzzy_satisfies_the_anchor_requirement(recall):
    """A cognate landing in desc must count as an anchor match, or the
    body-only filter would discard everything fuzzy just found."""
    b = _block(recall, id="x", desc="modello commerciale", body="pad " * 100)
    out = recall.top_k_blocks(
        [b], {"commercial"}, k=1, require_anchor=True, fuzzy=True
    )
    assert [x.id for x in out] == ["x"]


def test_fuzzy_off_by_default_keeps_exact_semantics(recall):
    b = _block(recall, id="catalogo", desc="catalogo unico")
    assert recall.top_k_blocks([b], {"catalogue"}, k=1) == []


def test_italian_stopwords_cover_greetings_and_temporal_adverbs(recall):
    """`oggi` used to survive into scoring and collide with the `.oggi`
    suffix of a real collection name, injecting two blocks for "ciao come
    stai oggi"."""
    stop = recall.resolve_stopwords({"AGD_RECALL_LANGS": "it"})
    assert not recall.strip_stopwords(
        recall.tokenize("ciao come stai oggi"), stop
    )
    # `altro` is a real category value in a live corpus — not a stopword.
    assert "altro" not in stop


def test_skip_superseded_drops_replaced_blocks(recall):
    """A block a later entry replaced must not be recalled as current."""
    stale = _block(recall, id="old-truth", desc="deploy procedure")
    stale.status = "superseded"
    fresh = _block(recall, id="new-truth", desc="deploy procedure")
    out = recall.top_k_blocks(
        [stale, fresh], {"deploy"}, k=3, skip_superseded=True
    )
    assert [b.id for b in out] == ["new-truth"]


def test_recency_breaks_ties_before_body_length(recall):
    old = _block(recall, id="a-old", desc="deploy", body="x")
    new = _block(recall, id="b-new", desc="deploy", body="x")
    old.updated, new.updated = "2026-01-01", "2026-07-25"
    assert [b.id for b in recall.top_k_blocks([old, new], {"deploy"}, k=2)] == [
        "b-new", "a-old",
    ]


def test_recency_key_tolerates_missing_and_malformed_dates(recall):
    assert recall._recency_key(_block(recall, id="x")) == 0
    b = _block(recall, id="y")
    b.updated = "not-a-date"
    assert recall._recency_key(b) == 0


def test_token_budget_truncates_low_scoring_blocks_first(recall):
    big_body = "x " * 200  # ~400 chars body
    blocks = [
        _block(recall, id="hi-1", desc="blog important", body=big_body),
        _block(recall, id="hi-2", desc="blog important", body=big_body),
        _block(recall, id="hi-3", desc="blog", body=big_body),
    ]
    chosen = recall.top_k_blocks(blocks, {"blog", "important"}, k=3)
    # Char-budget so small that only 1 fits.
    kept = recall.fits_budget(chosen, budget_chars=200)
    assert len(kept) == 1
    # The highest-scored one survives.
    assert kept[0].id in ("hi-1", "hi-2")


# -- should_skip -------------------------------------------------------------

def test_skip_when_prompt_empty(recall):
    assert recall.should_skip("", {}) == "prompt_empty"
    assert recall.should_skip("   ", {}) == "prompt_empty"


def test_skip_when_prompt_below_min_words(recall):
    assert recall.should_skip("ok", {}) is not None
    assert recall.should_skip("the and", {}) == "prompt_below_min_words"


def test_skip_when_prompt_too_long(recall):
    prompt = "x" * 5000
    assert recall.should_skip(prompt, {}) == "prompt_too_long"


def test_skip_when_prompt_looks_like_code_fence(recall):
    prompt = "ecco il codice:\n```\ndef foo():\n    pass\n```\n"
    assert recall.should_skip(prompt, {}) == "code_paste_detected"


def test_skip_when_prompt_looks_like_code_indent_ratio(recall):
    prompt = "\n".join([
        "intro line",
        "    indented one",
        "    indented two",
        "    indented three",
        "    indented four",
        "    indented five",
    ])
    assert recall.should_skip(prompt, {}) == "code_paste_detected"


def test_skip_when_AGD_RECALL_DISABLED_set(recall):
    out = recall.should_skip(
        "ricordi le mie preferenze blog?",
        {"AGD_RECALL_DISABLED": "1"},
    )
    assert out == "disabled_via_env"


# -- skip when external prerequisites missing --------------------------------

def test_skip_when_agd_binary_missing(recall, monkeypatch, tmp_memory):
    monkeypatch.setenv("AGD_BIN", "/nonexistent/agd-binary")
    stdin = io.StringIO(json.dumps({
        "user_prompt": "ricordi le mie preferenze blog?",
    }))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    assert rc == 0
    assert captured.getvalue() == ""


def test_skip_when_memory_file_missing(recall, monkeypatch, fake_agd, tmp_path):
    # Point AGD_MEMORY_FILE at a non-existent file
    monkeypatch.setenv("AGD_MEMORY_FILE", str(tmp_path / "missing.agd"))
    stdin = io.StringIO(json.dumps({
        "user_prompt": "ricordi le mie preferenze blog?",
    }))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    assert rc == 0
    assert captured.getvalue() == ""


# -- emit + main end-to-end --------------------------------------------------

def test_emit_format_includes_header_and_block_count(
    recall, monkeypatch, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {
            "id": "user-blog",
            "kind": "x-user",
            "desc": "preferenze sul blog",
            "body": "scrive blog post in italiano con apostrofi vintage",
            "refs": [],
        },
        {
            "id": "feedback-no-publish",
            "kind": "x-feedback",
            "desc": "non pubblicare senza review",
            "body": "git push solo dopo ok esplicito",
            "refs": [],
        },
    ]})
    stdin = io.StringIO(json.dumps({
        "user_prompt": "ricordi le mie preferenze blog?",
    }))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    out = captured.getvalue()
    assert rc == 0
    assert out.startswith("[agd-memory:auto-recall]")
    assert "block(s) selected" in out
    assert "#user-blog" in out


def test_emit_empty_on_skip(recall, monkeypatch, fake_agd, tmp_memory):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-blog", "kind": "x-user", "desc": "blog", "body": "x"},
    ]})
    stdin = io.StringIO(json.dumps({"user_prompt": "ok"}))  # too short
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    assert rc == 0
    assert captured.getvalue() == ""


def test_main_never_raises_even_when_subprocess_fails(
    recall, monkeypatch, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-blog", "kind": "x-user", "desc": "blog", "body": "x"},
    ], "get_fails": True})
    stdin = io.StringIO(json.dumps({
        "user_prompt": "ricordi le mie preferenze blog?",
    }))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    # get failed but recall must fall back to internal rendering
    assert rc == 0
    assert "[agd-memory:auto-recall]" in captured.getvalue()


def test_main_reads_user_prompt_from_stdin_json(
    recall, monkeypatch, fake_agd, tmp_memory
):
    fake_agd.write_fixture({"blocks": [
        {"id": "user-blog", "kind": "x-user", "desc": "preferenze blog", "body": ""},
    ]})
    stdin = io.StringIO(json.dumps({
        "session_id": "abc",
        "hook_event_name": "UserPromptSubmit",
        "user_prompt": "ricordi le mie preferenze blog?",
        "cwd": "/tmp",
    }))
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    assert rc == 0
    assert "#user-blog" in captured.getvalue()


def test_main_swallows_invalid_stdin_json(recall, monkeypatch, fake_agd, tmp_memory):
    stdin = io.StringIO("this is not json")
    captured = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured)
    rc = recall.main(stdin=stdin, env=dict(os.environ))
    assert rc == 0
    assert captured.getvalue() == ""


# -- config CLI --------------------------------------------------------------

def test_config_show_prints_default_langs(recall, capsys):
    rc = recall.run_config(["show"], dict(os.environ))
    out = capsys.readouterr().out
    assert rc == 0
    assert "langs:" in out
    assert "en" in out


def test_config_enable_persists_to_disk(recall, capsys):
    env = dict(os.environ)
    rc = recall.run_config(["enable", "it"], env)
    out = capsys.readouterr().out
    assert rc == 0
    assert "enabled it" in out
    # Re-read via resolve_langs (fresh env, no AGD_RECALL_LANGS).
    langs = recall.resolve_langs(env)
    assert "it" in langs
    assert "en" in langs


def test_config_enable_rejects_unknown_lang(recall, capsys):
    rc = recall.run_config(["enable", "klingon"], dict(os.environ))
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown built-in lang" in err


def test_config_disable_removes_lang(recall, capsys):
    env = dict(os.environ)
    recall.run_config(["enable", "it"], env)
    capsys.readouterr()  # clear
    rc = recall.run_config(["disable", "en"], env)
    assert rc == 0
    langs = recall.resolve_langs(env)
    assert "en" not in langs
    assert "it" in langs


def test_config_set_replaces_list(recall, capsys):
    env = dict(os.environ)
    recall.run_config(["enable", "it"], env)
    capsys.readouterr()
    rc = recall.run_config(["set", "en"], env)
    capsys.readouterr()
    assert rc == 0
    assert recall.resolve_langs(env) == ["en"]


def test_config_reset_removes_file(recall, capsys):
    env = dict(os.environ)
    recall.run_config(["enable", "it"], env)
    capsys.readouterr()
    cfg_path = recall._config_path(env)
    assert cfg_path.is_file()
    rc = recall.run_config(["reset"], env)
    capsys.readouterr()
    assert rc == 0
    assert not cfg_path.is_file()
    assert recall.resolve_langs(env) == list(recall.DEFAULT_LANGS)


def test_config_stopwords_file_loads_extra_words(recall, tmp_path, capsys):
    sw_file = tmp_path / "extra.txt"
    sw_file.write_text("custom1\ncustom2\n")
    env = dict(os.environ)
    rc = recall.run_config(["stopwords-file", str(sw_file)], env)
    capsys.readouterr()
    assert rc == 0
    stop = recall.resolve_stopwords(env)
    assert "custom1" in stop
    assert "custom2" in stop


def test_config_stopwords_file_clear(recall, tmp_path, capsys):
    sw_file = tmp_path / "extra.txt"
    sw_file.write_text("custom1\n")
    env = dict(os.environ)
    recall.run_config(["stopwords-file", str(sw_file)], env)
    capsys.readouterr()
    rc = recall.run_config(["stopwords-file"], env)
    capsys.readouterr()
    assert rc == 0
    stop = recall.resolve_stopwords(env)
    assert "custom1" not in stop


def test_config_routed_via_main(recall, capsys):
    # main() should dispatch `config show` correctly
    rc = recall.main(
        argv=["recall.py", "config", "show"],
        stdin=io.StringIO(""),
        env=dict(os.environ),
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "langs:" in out


def test_env_var_overrides_config_file(recall, capsys):
    env = dict(os.environ)
    recall.run_config(["enable", "it"], env)
    capsys.readouterr()
    # env var must win over persisted config
    env2 = dict(env, AGD_RECALL_LANGS="en")
    assert recall.resolve_langs(env2) == ["en"]
