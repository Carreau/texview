import json

from conftest import make_pass, sug, write_json

from texreview.core import Store


def test_edit_persists_dict_value(basic_review):
    st = Store(basic_review / "review")
    st.edit("s1", {"new": "we demonstrate that", "tags": ["style"]})
    dec = json.loads(
        (basic_review / "review" / "decisions.json").read_text())
    (val,) = dec.values()
    assert val == {"status": "pending", "new": "we demonstrate that",
                   "tags": ["style"]}
    st2 = Store(basic_review / "review")
    s = st2.suggestion("s1")
    assert s["new"] == "we demonstrate that"
    assert s["edited"] is True
    assert s["status"] == "pending"


def test_bare_string_decisions_still_parse(basic_review):
    st = Store(basic_review / "review")
    key = st.suggestion("s1")["key"]
    write_json(basic_review / "review" / "decisions.json",
               {key: "accepted"})
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s1")["status"] == "accepted"
    assert "edited" not in st2.suggestion("s1")


def test_edit_survives_reemission(basic_review):
    st = Store(basic_review / "review")
    st.edit("s2", {"new": "THE"})
    make_pass(basic_review, "0002-again.json",
              [sug("dup", "the the", "the")])   # same content re-emitted
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s2")["new"] == "THE"


def test_edit_ignores_identity_fields(basic_review):
    st = Store(basic_review / "review")
    original = dict(st.suggestion("s1"))
    st.edit("s1", {"old": "HACK", "occurrence": 5, "file": "other.tex",
                   "reasoning": "kept"})
    s = Store(basic_review / "review").suggestion("s1")
    assert s["old"] == original["old"]
    assert s.get("occurrence") == original.get("occurrence")
    assert s["file"] == original["file"]
    assert s["reasoning"] == "kept"


def test_edit_unknown_id(basic_review):
    st = Store(basic_review / "review")
    assert st.edit("nope", {"new": "x"}) is None


def test_edit_combines_with_status(basic_review):
    st = Store(basic_review / "review")
    st.suggestion("s1")["status"] = "accepted"
    st.save()
    st.edit("s1", {"new": "we show"})
    st2 = Store(basic_review / "review")
    assert st2.suggestion("s1")["status"] == "accepted"
    assert st2.suggestion("s1")["new"] == "we show"
