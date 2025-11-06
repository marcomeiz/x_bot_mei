from writing_rules import detect_banned_elements


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