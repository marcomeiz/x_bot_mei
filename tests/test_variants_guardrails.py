import re

import os
import importlib

from writing_rules import detect_banned_elements
import variant_generators as vg
from variant_generators import (
    _one_sentence_per_line,
    _avg_words_per_line_between,
    _english_only,
    _no_banned_language,
    _range_ok,
)


def test_one_sentence_per_line_ok():
    text = "Cut noise.\nProtect thinking.\nGuard time."
    assert _one_sentence_per_line(text) is True


def test_one_sentence_per_line_fail():
    text = "Cut noise. Protect thinking.\nGuard time."
    assert _one_sentence_per_line(text) is False


def test_avg_words_per_line_between():
    text = "Kill meetings.\nGuard blocks.\nShip work."
    assert _avg_words_per_line_between(text, 1, 3) is True
    assert _avg_words_per_line_between(text, 4, 6) is False


def test_english_only_and_banned_language():
    assert _english_only("Guard time.") is True
    assert _english_only("Bloquea tiempo.") is False
    assert _no_banned_language("Maybe ship later.") == "hedging"
    assert _no_banned_language("game-changing system") == "cliche"
    assert _no_banned_language("instant results") == "hype"
    assert _no_banned_language("Ship work.") is None


def test_char_ranges():
    # short: ≤140
    assert _range_ok("short", "A" * 140)
    assert not _range_ok("short", "A" * 141)
    # mid: 140–230
    assert _range_ok("mid", "A" * 200)
    assert not _range_ok("mid", "A" * 139)
    assert not _range_ok("mid", "A" * 231)
    assert _range_ok("long", "A" * 260)
    assert not _range_ok("long", "A" * 239)


def test_detect_banned_elements():
    assert "contains hashtag" in detect_banned_elements("Do this #now")
    issues = detect_banned_elements("This, not that and do it")
    assert any("uses commas" in i for i in issues)
    # Plain 'and' should NOT be flagged; only the phrase 'and/or'
    assert not any("and/or" in i for i in issues)

    issues2 = detect_banned_elements("Decide and/or commit.")
    assert any("and/or" in i for i in issues2)


def test_detect_links_and_emojis():
    txt = "Read this http://example.com now."
    issues = detect_banned_elements(txt)
    assert any("contains link" in i for i in issues)

    txt2 = "Ship work 🚀."
    issues2 = detect_banned_elements(txt2)
    assert any("contains emoji" in i for i in issues2)


def test_enforcement_honors_env_toggles(monkeypatch):
    # Toggle off commas enforcement, keep conjunctions enforcement on
    monkeypatch.setenv("ENFORCE_NO_COMMAS", "0")
    monkeypatch.setenv("ENFORCE_NO_AND_OR", "1")
    import sys
    # Reload module to re-read env toggles at import time
    if "variant_generators" in sys.modules:
        importlib.reload(vg)
    else:
        import variant_generators as _  # noqa: F401

    text = "Do the work, not the theater."
    # Should not raise for comma when ENFORCE_NO_COMMAS=0
    vg._enforce_variant_compliance("TEST", text, format_profile=None, allow_analogy=False)

    # Now disable conjunctions enforcement too
    monkeypatch.setenv("ENFORCE_NO_AND_OR", "0")
    importlib.reload(vg)
    text2 = "Do the work and ship daily."
    vg._enforce_variant_compliance("TEST", text2, format_profile=None, allow_analogy=False)


def test_words_per_line_range_strict():
    # Construct lines with 5, 8, and 12 words
    l1 = "one two three four five."
    l2 = "one two three four five six seven eight."
    l3 = "one two three four five six seven eight nine ten eleven twelve."
    ok = "\n".join([l1, l2, l3])
    assert _avg_words_per_line_between(ok, 5, 12) is True
    bad = ok + "\n" + "one two three four."
    assert _avg_words_per_line_between(bad, 5, 12) is False
