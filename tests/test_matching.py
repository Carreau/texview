from texreview.core import Match, find_occurrences, locate

TEXT = "l1\nl2\nl3\nl4 ANCHOR here\nl5\nl6\nl7"


def test_find_occurrences():
    assert find_occurrences("abcabcab", "ab") == [0, 3, 6]
    assert find_occurrences("aaa", "aa") == [0, 1]   # overlapping hits
    assert find_occurrences("xyz", "q") == []


def test_ok_basic():
    m = locate(TEXT, {"old": "ANCHOR"})
    assert (m.state, m.count, m.line) == ("ok", 1, 4)
    assert TEXT[m.start:m.end] == "ANCHOR"
    assert m.context[m.ctx_start:m.ctx_start + 6] == "ANCHOR"


def test_bad_file_and_empty_old():
    assert locate(None, {"old": "x"}).state == "bad-file"
    assert locate(TEXT, {"old": ""}).state == "missing"
    assert locate(TEXT, {}).state == "missing"


def test_missing():
    m = locate(TEXT, {"old": "nope"})
    assert (m.state, m.count) == ("missing", 0)


def test_ambiguous_and_occurrence():
    text = "foo bar foo baz"
    assert locate(text, {"old": "foo"}).state == "ambiguous"
    m = locate(text, {"old": "foo", "occurrence": 2})
    assert m.state == "ok" and m.start == 8
    # out-of-range occurrence
    assert locate(text, {"old": "foo", "occurrence": 3}).state == "missing"
    assert locate(text, {"old": "foo", "occurrence": 0}).state == "missing"


def test_context_widths():
    s = {"old": "ANCHOR"}
    assert locate(TEXT, s, 0).context == "l4 ANCHOR here"
    assert locate(TEXT, s, 1).context == "l3\nl4 ANCHOR here\nl5"
    assert locate(TEXT, s, 2).context == "l2\nl3\nl4 ANCHOR here\nl5\nl6"
    assert locate(TEXT, s, 100).context == TEXT
    # default is one line either side
    assert locate(TEXT, s).context == locate(TEXT, s, 1).context


def test_context_multiline_anchor():
    m = locate(TEXT, {"old": "ANCHOR here\nl5"}, 0)
    assert m.state == "ok"
    assert m.context == "l4 ANCHOR here\nl5"
    assert m.context[m.ctx_start:m.ctx_start + len("ANCHOR here\nl5")] \
        == "ANCHOR here\nl5"


def test_context_at_file_edges():
    m = locate(TEXT, {"old": "l1"}, 2)
    assert m.state == "ok" and m.context.startswith("l1")
    m = locate(TEXT, {"old": "l7"}, 2)
    assert m.context.endswith("l7")


def test_match_dataclass_defaults():
    m = Match("missing")
    assert (m.start, m.end, m.count, m.line) == (-1, -1, 0, 0)
