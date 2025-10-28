import re

from writing_rules import detect_banned_elements
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
    assert _range_ok("short", "A" * 160)
    assert not _range_ok("short", "A" * 161)
    assert _range_ok("mid", "A" * 200)
    assert not _range_ok("mid", "A" * 179)
    assert _range_ok("long", "A" * 260)
    assert not _range_ok("long", "A" * 239)


def test_detect_banned_elements():
    assert "contains hashtag" in detect_banned_elements("Do this #now")
    issues = detect_banned_elements("This, not that and do it")
    assert any("uses commas" in i for i in issues)
    assert any("uses conjunction" in i for i in issues)

